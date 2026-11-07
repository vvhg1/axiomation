"""The edges as one session on one file per paper.

The paper is written out as `edges.md`, the edge sheet
(`questions.edge_sheet`): the cut the stages before it left, every block
under its line with its roles, `⟦+ <role>⟧` where a later segment starts,
and every group between its lines, under a header naming the paper's files.
The session writes each block's edges, each claim's standing and a done
mark under it, the parts it names and the ambient frame in the header; the
blocks' text, their lines and the group lines are fixed.

The answer is the edited sheet. It is valid when the sheet's check passes
on the cut's blocks (`edge_sheet.check`), the fixed lines and the text are
as shown, every claim has a standing, no edge carries `optional` and no part
`(standard)` (a hand key's notation), a part is a block's, and every
`file:` target is on the `Files:` line shown or a path the text writes
whole. A valid answer gives its
records, in the sheet's order: the legend's parts as entries, the edges,
the self marks, the standings and the ambient frame; its cut
lines as proposals and its flag lines as flags. A
standing on a block whose roles hold no claim adds the claim role, as a
part from the claim's words (`claims_added`): a `ROLES` correction, its
evidence the standing's words, which the build applies with the answer,
never weighed as a proposal. An
answer is used whole or not at all.

The build's first pass (and an update's fresh session) reads the sheet and
writes its lines by owner in `lines.md` instead: a heading `@@ <handle>`
for each block and group, its done mark, then its lines; the legend and
`Pervasive:` lines under `@@ the header` (`LINES_HEADER`, which no handle
can be: a handle holds no space). Its gate and its check lay them into
the sheet first (`laid`), and the laid sheet is the answer kept
(`Validation.kept`).

The check runs as the session's gate (`main`), outside its sandbox: when it
tries to end, at most `gates.ROUNDS` times, and, but in the first pass, with
`Gate.on_write` after each of its tool uses, in a write mode that skips an
unchanged file and leaves out what is merely unfinished.

The key is the kind, the stage, the template, every block as the sheet
shows it (its id, content hash, form and segments) in order, the groups
(kind, blocks) and the files: those in the paper's directory, so a file
written there (a run's output) changes the key; write outputs elsewhere.
A paper is one session: a sheet over the
chunk limit is refused when its question is built, since an edge's target
can lie anywhere in the paper.

A later pass is a fresh session on the sheet the pass before
it left, its done marks taken off, under the writer's brief followed by
`edges-review.md`; it is checked as the first pass is.

An update (`update_question`) is a session on the sheet carried
onto a new version: each changed block shown old beside new and owing a Δ
line per part, the blocks the rule may reach open on the conditions under
their reaching edges. Its gate runs the rule (`closure.impact_reach`) on
the writer's Δ lines and fixes every owner they leave closed. A follow-up
session is the same question on the answer before it: no text Δ owed, the
owners that answer left open fixed with their Δ lines, and only what it
revealed open.

An update of one session (`plain_update_question`) is asked as a queue
of the blocks the edit touched (`update_queue`), its answer the carried
sheet once every task is settled: the lines of every block it serves are
its own, a Δ
line is owed on each changed block and stands nowhere else, and the
re-cut blocks' roles are open on their block lines and `⟦+ role⟧` markers
(`with_open_roles`); the roles written are `ROLES` corrections
(`roles_written`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from axiomation.assembler import corrected_cut, occurrences, owned_runs
from axiomation.closure import CLOSING, EdgeGraph, Impact, entry_label, impact_reach
from axiomation.closure import Link as ClosureLink
from axiomation.cutter import CutRules, DisplayRule
from axiomation.cutter.blocks import SENTENCE_CLOSERS, SENTENCE_ENDS
from axiomation.cutter.correspondence import mathematics, mathematics_spans
from axiomation.cutter.correspondence import words as markup_words
from axiomation.cutter.model import (
    Block,
    Cut,
    GivenHandles,
    Role,
    Segment,
    role_set,
    roles_problem,
)
from axiomation.exchange import cut_from_payload, cut_payload, given_handles_payload
from axiomation.flags import Raised
from axiomation.identity import (
    BlockClass,
    ExcerptClass,
    Lineage,
    Link,
    Version,
    block_text,
)
from axiomation.identity import place as place_excerpt
from axiomation.questions import (
    Gate,
    Question,
    Stage,
    Template,
    Validation,
    Workspace,
    gates,
    load_template,
)
from axiomation.questions import edge_sheet as es
from axiomation.questions.cut_file import EXCERPT_WORDS, RenderError
from axiomation.questions.cut_review import question_key
from axiomation.questions.review_blocks import DONE_MARK
from axiomation.questions.roles_file import part_lines
from axiomation.reader import Document, read_paper
from axiomation.records import (
    CorrectionClass,
    CutCorrection,
    CutOp,
    Declared,
    Delta,
    Edge,
    EdgeKind,
    EdgeRecords,
    EdgeTarget,
    Entry,
    Flag,
    Frame,
    ImpactClass,
    LineKind,
    LineOrder,
    Mark,
    Partial,
    RecordError,
    SelfMark,
    Signal,
    Standing,
    Status,
    TargetKind,
    correction_data,
    correction_from_data,
)

KIND = "edges"
SESSION_TEMPLATE = "edges-file"
#: A later pass's section, after the writer's brief.
REVIEW_TEMPLATE = "edges-review"
EDGES_FILE = "edges.md"
KICKOFF = "Write the edges of edges.md, as your instructions say."
#: The build's first pass writes its lines by owner in a file of their own,
#: which the check lays into the sheet: the file, its section after the
#: writer's brief, its kickoff, the gate's spec field, and the heading of
#: the lines that go above the sheet's `---` line.
LINES_FILE = "lines.md"
LINES_TEMPLATE = "edges-lines"
LINES_KICKOFF = "Write the edges of edges.md in lines.md, as your instructions say."
LINES = "lines"
LINES_HEADER = "the header"
#: A later pass writes only its changes in `lines.md`, `+` and `-` lines
#: by owner, which the check applies to the sheet it was shown: its
#: section after the review's, and the gate's spec field holding that
#: sheet. Its last line is `DONE_MARK`, so a pass that changes nothing
#: still answers.
CHANGES_TEMPLATE = "edges-changes"
CHANGES = "changes"
REVIEW_KICKOFF = (
    "Review the edges of edges.md and write your changes in lines.md, as your "
    "instructions say."
)
#: The re-ask after a round of cut corrections.
REASK_TEMPLATE = "edges-reask"
REASK_KICKOFF = "Write the reopened blocks of edges.md, as your instructions say."
#: An update's session on the sheet carried onto a new version.
UPDATE_TEMPLATE = "edges-update"
UPDATE_KICKOFF = (
    "Write the Δ lines and the reopened blocks of edges.md, as your instructions say."
)
#: An update's one session: its gate's spec field.
PLAIN = "plain_update"
#: Why a re-cut block's roles were corrected: the update's one session
#: wrote them on its block line.
ROLES_WRITTEN_WHY = "roles written by the update's session on a re-cut block"
#: A re-cut block whose written roles could not be placed: a `role` flag's
#: note.
ROLES_NOT_PLACED = "the roles written here could not be placed"
#: The gate's spec field naming the blocks whose roles a person pinned, by
#: handle: a standing there adds no claim role.
CLAIMS_FIXED = "claims_fixed"
#: The start of a problem that names a Δ line still owed, which the check on
#: every write leaves out with what is unfinished.
OWED = "no Δ line ("
#: The sheet's header above its `Files:` line.
HEADER = (
    "# edges\n\n"
    "Parts' legend lines and the `Pervasive:` line go here, above the line "
    "below.\n"
)

#: The write mode's argument, after the spec and the edited file.
WRITE = "write"
#: How many problems a gate shows.
SHOWN = 20


# --- the question -------------------------------------------------------------------


def sheet_blocks(document: Document, cut: Cut) -> list[es.SheetBlock]:
    """The cut's blocks as the sheet shows them: their owned runs, their
    segments, their labels from their extents' text (a group's own label
    left to the group)."""
    first = {g.blocks[0] for g in cut.groups}
    found = []
    for block in cut.blocks:
        extent = document.text(block.extent)
        found.append(
            es.SheetBlock(
                id=block.id,
                form=block.form.value,
                runs=owned_runs(document, block),
                segments=_segments_shown(document, block),
                labels=tuple(
                    es.labels_in(
                        extent, es.group_label(extent) if block.id in first else None
                    )
                ),
                roles=tuple(role.value for role in block.roles),
            )
        )
    return found if cut.handles is None else _given(found, cut.handles)


def _given(blocks: Sequence[es.SheetBlock], given: GivenHandles) -> list[es.SheetBlock]:
    """Each block with its given handle; one without, a block a correction
    made, minted from its words past the given, the reserved and those
    minted before it."""
    taken = set(given.by_id.values()) | given.reserved
    found = []
    for b in blocks:
        handle = given.by_id.get(b.id)
        if handle is None:
            body = f"\n{es.RUN_BREAK}\n".join(b.runs)
            handle = es.free_handle(es.handle_for(body, b.labels), taken.__contains__)
            taken.add(handle)
        found.append(dataclasses.replace(b, handle=handle))
    return found


def _segments_shown(document: Document, block: Block) -> tuple[tuple[str, str], ...]:
    """A block's segments as the sheet shows them, `(starts, role)`. A later
    segment's start is its excerpt, unless that does not place once in the
    block's owned text, as when it takes in a `\\label` the block does not
    own: then the fewest owned words from where the assembler places it
    that do."""
    found = [(s.starts, s.role.value if s.role else es.NONE) for s in block.segments]
    if len(found) < 2:
        return tuple(found)
    body = f"\n{es.RUN_BREAK}\n".join(owned_runs(document, block))

    def places(words: str) -> int:
        pattern = re.compile(r"\s+".join(re.escape(w) for w in words.split()))
        return len(pattern.findall(body)) if words.split() else 0

    shown = [found[0]]
    for (starts, role), place in zip(
        found[1:], part_lines(document, block), strict=True
    ):
        if places(starts) == 1:
            shown.append((starts, role))
            continue
        run = next(r for r in block.owned if r.start <= place.offset < r.end)
        words = document.file(run.file).text[place.offset : run.end].split()
        excerpt = next(
            (
                " ".join(words[:n])
                for n in range(1, len(words) + 1)
                if places(" ".join(words[:n])) == 1
            ),
            starts,
        )
        shown.append((excerpt, role))
    return tuple(shown)


def sheet_groups(document: Document, cut: Cut) -> list[es.SheetGroup]:
    """The cut's groups as the sheet shows them; on a cut with given
    handles, each with its own, else one minted from its first block's,
    past the given and the reserved."""
    found = [
        es.SheetGroup(
            g.kind.value,
            g.blocks,
            es.group_label(document.text(cut.block(g.blocks[0]).extent)),
        )
        for g in cut.groups
    ]
    given = cut.handles
    if given is None:
        return found
    by_block = es.block_handles(sheet_blocks(document, cut))
    taken = set(given.by_id.values()) | given.reserved
    named = []
    for g, shown in zip(cut.groups, found, strict=True):
        handle = given.by_id.get(g.id)
        if handle is None:
            minted = es.group_handle(by_block[g.blocks[0]], shown.label)
            handle = es.free_handle(minted, taken.__contains__)
            taken.add(handle)
        named.append(dataclasses.replace(shown, handle=handle))
    return named


def session_question(
    document: Document,
    cut: Cut,
    template: Template,
    files: Sequence[str],
    limit: int | None = None,
    claims_fixed: Collection[str] = (),
) -> Question:
    """The paper's one session, on the cut the stages before it left;
    `files` are the paper's files on disk (`edge_sheet.paper_files`) and
    `limit` the characters a session reads at most (`None`: no limit);
    `claims_fixed`, the blocks whose roles a person gave (`_question`).
    It reads `edges.md` and writes its lines by owner in `lines.md`, under
    the writer's brief followed by `LINES_TEMPLATE`'s section; the check
    lays them into the sheet (`laid`), which is the answer kept."""
    lines = load_template(LINES_TEMPLATE)
    return _question(
        document,
        cut,
        template,
        files,
        limit,
        instructions=template.render() + "\n\n" + lines.render(),
        prompt=LINES_KICKOFF,
        key_extra={"lines": lines.version},
        claims_fixed=claims_fixed,
        lines=True,
    )


def review_question(
    document: Document,
    cut: Cut,
    writer: Template,
    review: Template,
    files: Sequence[str],
    previous: str,
    follows: str,
    pass_: int,
    limit: int | None = None,
    rule_flags: Sequence[Raised] = (),
) -> Question:
    """A later pass: a fresh session on the sheet the pass
    before it left (`previous`, its answer, keyed `follows`), its claims'
    handles written (`es.with_handles`), under the writer's brief, the
    review's section and `CHANGES_TEMPLATE`'s, and the rules' flags on the
    sheet's edges where it is shown them (positions, each by its block's
    handle). It reads
    `edges.md` and writes only its changes in `lines.md`; the check
    applies them to the sheet shown (`applied_changes`), which is the
    answer kept, and reads it as the first pass's. Its key adds the pass,
    the key of the answer it follows (not the sheet's text: a pass is found
    again without reading it), the writer's brief, the changes' section and
    the flags shown, so a new answer before it asks it again."""
    shown = es.with_handles(previous)
    changes = load_template(CHANGES_TEMPLATE)
    instructions = writer.render() + "\n\n" + review.render()
    key_extra: dict[str, Any] = {
        "pass": pass_,
        "follows": follows,
        "writer": writer.version,
        "changes": changes.version,
    }
    noted = _rule_notes(document, cut, rule_flags)
    if noted:
        instructions += "\n\n" + RULE_FLAGS_NOTE + "\n".join(noted) + "\n"
        key_extra["rule_flags"] = noted
    return _question(
        document,
        cut,
        review,
        files,
        limit,
        shown=shown,
        instructions=instructions + "\n\n" + changes.render(),
        prompt=REVIEW_KICKOFF,
        key_extra=key_extra,
        spec_extra={CHANGES: shown},
        lines=True,
    )


#: What the review pass is told of the rules' flags.
RULE_FLAGS_NOTE = """### Positions the rules took

Two checks ran over the edges as written. Each marks a place worth a
second look, not a fault: on the hand keys both fire on edges that are
right.

- `qualifies`: every qualifies edge. Does the assumption constrain that
  definition, or is the edge a `uses`, or no edge at all?
- `definition-uses-claim`: a block that defines something uses a whole
  block that only claims. Does the definition rely on the claim, or on a
  part of it, or not at all?

"""


def _rule_notes(document: Document, cut: Cut, flags: Sequence[Raised]) -> list[str]:
    """One line per rule flag on the sheet's blocks: `- <source> →
    <target>: <signal>`, by the sheet's handles."""
    if not flags:
        return []
    named = es.block_handles(sheet_blocks(document, cut))
    return [
        f"- {' → '.join(named.get(b, b) for b in r.flag.blocks)}: {r.flag.signal.value}"
        for r in flags
    ]


def reopen(sheet: str, blocks: Collection[str] | None = None) -> str:
    """`sheet` with the done marks of `blocks` (handles; every block when
    None) taken off, so a review pass reads each of them again, and its
    claims' handles written (`es.with_handles`). A re-ask may
    reopen only the blocks a cut correction touches."""
    kept = []
    for line in sheet.split("\n"):
        done = es.DONE.match(line)
        if done and (blocks is None or done.group(1) in blocks):
            continue
        kept.append(line)
    return es.with_handles("\n".join(kept))


# --- the re-ask after a cut correction -------------------------------


@dataclass(frozen=True, slots=True)
class Hinted:
    """A fuzzy hint for an excerpt carried across versions: shown
    to the writer beside the sheet, never written into it."""

    handle: str
    excerpt: str
    words: str
    block: str


@dataclass(frozen=True, slots=True)
class CarriedExcerpt:
    """A quote carried across versions: OLD's words, the handle of the NEW
    block or group it went to
    (None: a deleted block's, its words nowhere), and how it was carried:
    `found` for a same, moved or markup block's (verbatim or re-quoted),
    `removed` for words a markup, maths or macro change ran through that
    cannot be re-quoted, else `identity.place`'s class."""

    block: str | None
    excerpt: str
    cls: ExcerptClass


@dataclass(frozen=True, slots=True)
class Gone:
    """An edge line dropped across versions because the block it pointed
    at was deleted and its words are nowhere: the handle of its source in
    NEW, the line as OLD wrote it, and the deleted block's OLD handle and
    id."""

    source: str
    line: str
    target: str
    old: str


@dataclass(frozen=True, slots=True)
class Carried:
    """A sheet carried onto a corrected cut: the sheet, the handles of the
    blocks and groups reopened (their done marks off), and why each is;
    across versions, the fuzzy hints beside it, every quote taken across,
    the changed block each reopen comes from, and each edge line dropped
    with its deleted target."""

    sheet: str
    reopened: tuple[str, ...]
    why: dict[str, str]
    hints: tuple[Hinted, ...] = ()
    excerpts: tuple[CarriedExcerpt, ...] = ()
    #: across versions, the changed block each reopen comes from, by
    #: handle: NEW's block, or `DELETED_BLOCK` and OLD's id
    causes: Mapping[str, str] = dataclasses.field(default_factory=dict)
    gone: tuple[Gone, ...] = ()


#: The lines a writer writes under a block or after a group's end line.
_WRITTEN = ("→ ", "⊢ ", es.CUT_MARK, f"{es.DELTA_MARK} ")


def written_lines(sheet: str, flags: bool = False) -> dict[str, list[str]]:
    """Each block's and group's own lines, by handle: its edges, standings,
    cut lines and done mark, as written; with `flags`, its flag lines too,
    which the fixed lines leave out. A person's keep mark is left out."""
    found: dict[str, list[str]] = {}
    owner: str | None = None
    for line in sheet.split("\n"):
        # a person's keep mark is the person's answer's, read by its pins
        # alone: the line is the line it holds
        line, _ = es.unkept(line)
        if m := es.BLOCK.match(line):
            owner = m.group(1)
        elif es.GROUP.match(line):
            owner = None
        elif m := es.END.match(line):
            owner = m.group(1)
        elif owner is not None and (
            line.startswith(_WRITTEN)
            or es.DONE.match(line)
            or (flags and line.startswith(f"{es.FLAG_MARK} "))
        ):
            found.setdefault(owner, []).append(line)
    return found


def handles(document: Document, cut: Cut) -> dict[str, str]:
    """Each block's and group's handle on the sheet of `cut`, to its id, as
    a question's `handles`."""
    blocks, groups = _named_by(document, cut)
    return {h: i for i, h in blocks.items()} | {h: i for i, h in groups.items()}


def named(document: Document, cut: Cut) -> Cut:
    """`cut` with its sheet's handles given, so that a correction carries
    each block's handle with its node (`assembler`) rather than mint it
    again from its words: its own where it has them, else those its words
    give now."""
    if cut.handles is not None:
        return cut
    by_id = {ident: h for h, ident in handles(document, cut).items()}
    return dataclasses.replace(cut, handles=GivenHandles(by_id))


def _named_by(document: Document, cut: Cut) -> tuple[dict[str, str], dict[str, str]]:
    """The sheet's handle of each block and each group of `cut`, by id."""
    blocks = es.block_handles(sheet_blocks(document, cut))
    groups = es.group_handles(sheet_groups(document, cut), blocks)
    return blocks, {g.id: h for g, h in zip(cut.groups, groups, strict=True)}


def carry(
    document: Document,
    previous: str,
    before: Cut,
    after: Cut,
    files: Sequence[str],
    *,
    reopen: Mapping[str, str] | None = None,
    cuts_weighed: bool = True,
    new: Document | None = None,
    lineage: Lineage | None = None,
) -> Carried:
    """`previous`, the sheet written on `before`, carried onto `after`. Given
    `lineage`, `after` is a cut of another version,
    `new`, and the sheet is carried across versions (`_carry_across`);
    else `before` and `after` are two cuts of `document`, and an
    unchanged block keeps its lines and its done mark.
    A changed block's lines go to the block of `after` that holds their
    words (an edge's or self mark's evidence, a standing's claim), else to
    the one that holds its start. A target that changed follows its part's
    words, as does the legend line; a whole-block target goes to the block
    that holds the old one's start, and its source is reopened. Every block
    a correction made or changed is reopened.

    A group `after` holds and `before` does not is reopened, for its
    justifies edge; a group `before` holds and `after` does not leaves its
    lines on its first block, which is reopened, as is the source of each
    about edge onto it. `reopen` reopens more, by handle on
    `after`, each with why. The cut lines are dropped where a round weighed
    them (`cuts_weighed`), else carried, and their source reopened where
    the block they name changed."""
    if lineage is not None:
        return _carry_across(
            document,
            new or document,
            previous,
            before,
            after,
            files,
            lineage,
            reopen or {},
            cuts_weighed,
        )
    old_blocks, old_groups = _named_by(document, before)
    new_blocks, new_groups = _named_by(document, after)
    old_of = {h: i for i, h in old_blocks.items()} | {
        h: i for i, h in old_groups.items()
    }
    kept = {(b.id, b.hash) for b in before.blocks}
    changed = [b for b in after.blocks if (b.id, b.hash) not in kept]
    gone = {
        b.id: b
        for b in before.blocks
        if (b.id, b.hash) not in {(a.id, a.hash) for a in after.blocks}
    }
    runs = {
        b.id: [es.norm(r) for r in sb.runs]
        for b, sb in zip(after.blocks, sheet_blocks(document, after), strict=True)
    }
    why: dict[str, str] = {
        new_blocks[b.id]: "a cut correction made or changed it" for b in changed
    }
    old_group_ids = {g.id for g in before.groups}
    for group in after.groups:
        if group.id not in old_group_ids and len(group.blocks) > 1:
            why.setdefault(new_groups[group.id], "the groups made it: give its edges")
    dissolved = {
        handle
        for ident, handle in old_groups.items()
        if ident not in new_groups
        and len(next(g for g in before.groups if g.id == ident).blocks) > 1
    }

    def became(old: str) -> list[Block]:
        """The new blocks over the old block `old`'s place."""
        block = gone[old]
        return [
            b
            for b in changed
            if b.extent.file == block.extent.file
            and b.extent.start < block.extent.end
            and block.extent.start < b.extent.end
        ]

    def holders(old: str, words: str) -> list[str]:
        """The new blocks `old` became that hold `words` within one run."""
        found = es.norm(words)
        return [
            b.id
            for b in became(old)
            if found and any(found in run for run in runs[b.id])
        ]

    def holding(old: str, words: str) -> str:
        """The new block, among those `old` became, that holds `words`, else
        the one that holds `old`'s start."""
        block, near = gone[old], became(old)
        found = holders(old, words)
        if found:
            return found[0]
        start = block.owned[0].start
        for b in near:
            if any(r.start <= start < r.end for r in b.owned):
                return b.id
        return near[0].id if near else changed[0].id

    def renamed(handle: str, words: str = "") -> tuple[str, bool]:
        """A handle of `previous` as `after` names it, and whether it moved
        from a block a correction changed."""
        ident = old_of.get(handle)
        if ident is None:
            return handle, False  # scope:paper, file:…
        if ident in old_groups:
            group = new_groups.get(ident)
            if group is not None:
                return group, False
            first = next(g for g in before.groups if g.id == ident).blocks[0]
            ident = first
        if ident in gone:
            return new_blocks[holding(ident, words)], True
        return new_blocks[ident], False

    sheet = es.parse(previous)
    id_of = {h: i for i, h in new_blocks.items()}

    # Each part follows its words to a new block; a label two merged blocks
    # share is told apart by a suffix, so neither part is lost.
    parts: dict[tuple[str, str], tuple[str, str, bool]] = {}
    taken: set[tuple[str, str]] = set()
    header: list[str] = []
    for (target, label), words in sheet.legend.items():
        new, moved = renamed(target, words)
        fresh, n = label, 1
        while (new, fresh) in taken:
            n += 1
            fresh = f"{label}-{n}"
        taken.add((new, fresh))
        # A moved part is placed only where its words stand within one run
        # of one of the new blocks alone.
        ident = old_of[target]
        placed = not moved or (ident in gone and len(holders(ident, words)) == 1)
        parts[(target, label)] = (new, fresh, placed)
        role = sheet.legend_roles.get((target, label))
        header.append(f'  {new} [{fresh}]: "{words}"' + (f" ({role})" if role else ""))
    if sheet.pervasive:
        named = dict.fromkeys(renamed(h)[0] for h in sheet.pervasive)
        header.append(f"Pervasive: {', '.join(named)}")

    lines: dict[str, list[str]] = {}
    for owner, owned in written_lines(previous, flags=True).items():
        for line in owned:
            if es.DONE.match(line) or (cuts_weighed and line.startswith(es.CUT_MARK)):
                continue
            if line.startswith(f"{es.DELTA_MARK} "):
                continue  # an update's, which no later pass takes
            if m := es.EDGE.match(line):
                source, _ = renamed(owner, m.group("evidence"))
                if m.group("target") in dissolved:
                    why.setdefault(
                        source,
                        f"its edge onto {m.group('target')}, a group now dissolved, "
                        "needs a target",
                    )
                line = _retargeted(line, m, source, parts, renamed, why)
            elif m := es.SUPPORT.match(line):
                source, _ = renamed(owner, m.group("claim"))
            elif m := es.FLAG_LINE.match(line):
                source, _ = renamed(owner, m.group("evidence"))
            elif m := es.CUT.match(line):
                source, _ = renamed(owner)
                target = m.group("target")
                if target:
                    new, moved = renamed(target)
                    if moved or new != target:
                        why.setdefault(
                            source,
                            f"its cut line names {target}, which a cut correction "
                            "changed",
                        )
                    line = line[: m.start("target")] + new + line[m.end("target") :]
            else:
                source, _ = renamed(owner)
            if owner in dissolved:
                why.setdefault(
                    source,
                    f"it holds the lines of {owner}, a group now dissolved",
                )
            lines.setdefault(source, []).append(line)
    # A line whose words a correction cut through, as a split inside a
    # group's block can a group's evidence: its owner says them again.
    members = {new_groups[g.id]: g.blocks for g in after.groups}
    for owner, owned in lines.items():
        for line in owned:
            words = ""
            if m := es.EDGE.match(line):
                words = m.group("evidence")
            elif m := es.SUPPORT.match(line):
                words = m.group("claim")
            held = members.get(owner, (id_of.get(owner, ""),))
            if words and not any(
                es.norm(words) in run for b in held for run in runs.get(b, [])
            ):
                why.setdefault(
                    owner,
                    "a cut correction cut through the words of one of its lines",
                )
    # Two edges of one kind onto blocks a merge made one: the session says
    # which stands.
    for source, owned in lines.items():
        aims = [
            (m.group("kind"), m.group("target"))
            for line in owned
            if (m := es.EDGE.match(line)) and m.group("kind") != es.SELF
        ]
        if len(aims) != len(set(aims)):
            why.setdefault(
                source, "two of its edges now point at one block, which a merge made"
            )

    return _rendered(document, after, files, header, lines, why, reopen or {})


def _rendered(
    document: Document,
    after: Cut,
    files: Sequence[str],
    header: list[str],
    lines: dict[str, list[str]],
    why: dict[str, str],
    reopen: Mapping[str, str],
    hints: tuple[Hinted, ...] = (),
    was: Mapping[str, Sequence[str]] | None = None,
) -> Carried:
    """The carried sheet: `after` rendered on `document`, the legend and
    pervasive lines in `header`, each block's and group's `lines`, a done
    mark on each one not reopened (`why`, with `reopen` added); `was`, a
    changed block's old texts, under its line."""
    for handle, reason in reopen.items():
        why[handle] = f"{reason}; {why[handle]}" if handle in why else reason
    new_blocks, new_groups = _named_by(document, after)
    # a reopened group's line onto a target carries its members' onto it,
    # so a member's line out of the group is the same question
    # (`group_line_problems`)
    for group in after.groups:
        handle = new_groups[group.id]
        if handle not in why:
            continue
        inside = {handle, *(new_blocks[b] for b in group.blocks)}
        for b in group.blocks:
            member = new_blocks[b]
            if member in why:
                continue
            if any(
                (m := es.EDGE.match(es.unkept(line)[0]))
                and m.group("kind") == "justifies"
                and m.group("target") not in inside
                for line in lines.get(member, [])
            ):
                why[member] = (
                    f"a member of {handle}, which is reopened: a justifies line of "
                    f"it onto the target {handle}'s line names goes, the group's "
                    "line carries it"
                )
    every = [*new_blocks.values(), *new_groups.values()]
    reopened = tuple(h for h in every if h in why)
    rendered = es.render(
        sheet_blocks(document, after), sheet_groups(document, after), files, HEADER
    )
    out: list[str] = []
    owner: str | None = None

    def close() -> None:
        if owner is not None:
            done = [] if owner in why else [f"⟦done {owner}⟧"]
            out.extend([*lines.get(owner, []), *done, ""])

    for line in rendered.split("\n"):
        if line.startswith("Files: "):
            out.extend(header)
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            close()
            owner = None if es.GROUP.match(line) else m.group(1)
        out.append(line)
        if es.BLOCK.match(line) and owner is not None:
            out.extend(f"{es.WAS}{text}" for text in (was or {}).get(owner, ()))
    close()
    return Carried(
        es.with_handles("\n".join(out)),
        reopened,
        {h: why[h] for h in reopened},
        hints,
    )


#: The classes whose block is reopened across versions: its text changed
#: in words, maths or macros, or it was split, merged or added. Same and
#: moved blocks keep their lines and done mark; markup only closes at the
#: block; a move's `[position]` edges are
#: an update's to ask (`reopen`).
_REOPENS = {
    BlockClass.MATH: "its maths changed",
    BlockClass.MACRO: "a macro it uses changed",
    BlockClass.CHANGED: "its words changed",
    BlockClass.SPLIT: "it was split",
    BlockClass.MERGED: "it was merged",
    BlockClass.ADDED: "it is new in this version",
}
#: The classes whose block keeps its words but for markup, maths or a macro:
#: a quote across the change is re-quoted on the new text (`_requoted`).
_REQUOTED = (BlockClass.MARKUP, BlockClass.MATH, BlockClass.MACRO)


#: The classes whose block has one heir that keeps its parts' labels when
#: their words are lost to the edit.
_ONE_HEIR = (BlockClass.MARKUP, BlockClass.MATH, BlockClass.MACRO, BlockClass.CHANGED)


def _heir_of(link: Link | None) -> str | None:
    """The one block of NEW a markup, maths, macro or word change of an OLD
    block became; None for any other link."""
    if link is None or link.cls not in _ONE_HEIR or len(link.new) != 1:
        return None
    return link.new[0]


def _requoted(words: str, old_text: str, new_text: str) -> str | None:
    """`words`, quoted from `old_text`, as `new_text` reads them: the
    quote's span carried through a character alignment of the two texts
    (difflib's opcodes), an end inside a replaced run taking all of it and
    markup inserted at an end taken in. A markup, maths or macro change
    keeps a text's shape (`_shape`: its words outside markup and maths,
    and how many runs of maths it holds), so the image stands only where
    its shape is the quote's, the text before it has the shape of the text
    before the quote, it is found once in `new_text` and it closes what it
    opens; else `None`, as when the words are not found once in
    `old_text`. Words `old_text` holds more than once stand where
    `new_text` still holds them."""
    found = es.norm(words)
    if not found or _occurrences(found, old_text) != 1:
        return found if found and found in new_text else None
    start = old_text.index(found)
    end = start + len(found)
    ops = SequenceMatcher(None, old_text, new_text, autojunk=False).get_opcodes()
    new_start = new_end = None
    for n, (op, i1, i2, j1, j2) in enumerate(ops):
        if new_start is None and op == "insert" and i1 == start:
            if _shape(new_text[j1:j2]) == ((), 0):
                new_start = j1
        elif new_start is None and i1 <= start < i2:
            new_start = j1 + (start - i1) if op == "equal" else j1
        if i1 < end <= i2:
            new_end = j1 + (end - i1) if op == "equal" else j2
            if end == i2 and n + 1 < len(ops):
                op2, k1, _, l1, l2 = ops[n + 1]
                if op2 == "insert" and k1 == end and _shape(new_text[l1:l2]) == ((), 0):
                    new_end = l2
            break
    if new_start is None or new_end is None:
        return None
    image = new_text[new_start:new_end].strip()
    if not image or _occurrences(image, new_text) != 1:
        return None
    at = new_text.index(image)
    if not (
        _edge(old_text, start - 1) <= _edge(new_text, at - 1)
        and _edge(old_text, end) <= _edge(new_text, at + len(image))
    ):
        return None  # it starts or ends inside a word or a command
    # the same shape before it: the alignment did not drift to a twin
    if _shape(new_text[: new_text.index(image)]) != _shape(old_text[:start]):
        return None
    if image == found:
        return found  # the change did not touch it
    if _shape(old_text) != _shape(new_text):
        return None  # not a markup, maths or macro change after all
    run = _maths_run(old_text, start, end)
    if run is not None:
        # a quote taken inside one run of maths: its words are maths, which
        # the change may rewrite, so its image stands inside the same run
        if _maths_run(new_text, at, at + len(image)) != run:
            return None
        return image if _braces_closed(image) or not _braces_closed(found) else None
    if _shape(image) != _shape(found):
        return None
    if _balanced(found) and not _balanced(image):
        return None  # it cuts through maths or a command's argument
    return image


def _maths_run(text: str, start: int, end: int) -> int | None:
    """Which run of maths in `text` holds `text[start:end]` within its
    delimiters, by its place among the runs; None where none does."""
    for n, (a, b) in enumerate(mathematics_spans(text)):
        if a < start and end < b:
            return n
    return None


#: A run of inline maths, for `_balanced`.
_MATHS = re.compile(r"(?<!\\)\$[^$]*\$")


def _edge(text: str, at: int) -> bool:
    """Whether `text` breaks at `at`: its start or end, or a space."""
    return not 0 <= at < len(text) or text[at].isspace()


def _shape(text: str) -> tuple[tuple[str, ...], int]:
    """What a markup, maths or macro change keeps of a text: its words
    outside markup and maths (`correspondence.words`, identity's
    markup-blind key) and how many runs of maths it holds."""
    return markup_words(text), len(mathematics(text))


def _balanced(text: str) -> bool:
    """Whether `text` closes every maths run, brace and Markdown emphasis
    it opens (braces in order; `_` outside maths, where it is a
    subscript)."""
    dollars = text.count("$") - text.count("\\$")
    prose = _MATHS.sub(" ", text)
    if prose.count("*") % 2 or prose.count("_") % 2:
        return False
    return dollars % 2 == 0 and _braces_closed(text)


def _braces_closed(text: str) -> bool:
    """Whether `text` closes every brace it opens, in order."""
    depth = 0
    for char in text.replace("\\{", "").replace("\\}", ""):
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth < 0:
            return False
    return depth == 0


def _occurrences(needle: str, hay: str) -> int:
    """How often `needle` stands in `hay`, overlapping ones counted: "a a"
    stands twice in "a a a"."""
    found, at = 0, hay.find(needle)
    while at != -1:
        found += 1
        at = hay.find(needle, at + 1)
    return found


def _carry_across(
    old: Document,
    new: Document,
    previous: str,
    before: Cut,
    after: Cut,
    files: Sequence[str],
    lineage: Lineage,
    reopen: Mapping[str, str],
    cuts_weighed: bool,
) -> Carried:
    """`previous`, written on `before`, a cut of `old`, carried onto
    `after`, a cut of `new`, through `lineage`. A same or
    moved block keeps its lines and done mark verbatim. A line of another
    block follows its words (an edge's or self mark's evidence, a standing's
    claim, a flag's evidence) by `identity.place`: found once, it goes where
    they are; ambiguous or removed, it is demoted to the block the old one
    became and that block reopened, the fuzzy hint returned beside the
    sheet, never written. A legend part follows its words alike; an edge
    whose part was not found once reopens its source. A line of a deleted
    block whose words are nowhere is dropped, and an edge onto a deleted
    block whose words are nowhere is dropped, its source reopened with the
    line quoted, whatever reopened it first, and the line returned
    (`Carried.gone`)."""
    old_blocks, old_groups = _named_by(old, before)
    new_blocks, new_groups = _named_by(new, after)
    old_of = {h: i for i, h in old_blocks.items()} | {
        h: i for i, h in old_groups.items()
    }
    version = Version(new, after)
    was = Version(old, before)
    why: dict[str, str] = {}
    causes: dict[str, str] = {}
    hints: list[Hinted] = []
    excerpts: list[CarriedExcerpt] = []
    gone: list[Gone] = []

    def reopen_by(handle: str, reason: str, cause: str) -> None:
        """`handle` reopened for `reason`, the first reason holding, and
        the changed block it comes from (`cause_of`) with it."""
        if handle not in why:
            why[handle] = reason
            causes[handle] = cause

    def cause_of(ident: str) -> str:
        """OLD block `ident`'s heir as `after` names it: the changed block
        a reopen comes from; `DELETED_BLOCK` and `ident` where it has none."""
        link = lineage.link_of_old(ident)
        new_of = link.new if link is not None else ()
        target = link.heir if link is not None and link.heir in new_of else None
        target = target or (new_of[0] if new_of else None)
        return new_blocks[target] if target else f"{DELETED_BLOCK}{ident}"

    def noted(block: str | None, said: str | None, cls: ExcerptClass) -> None:
        """A quote taken across, for the update's export."""
        if said:
            excerpts.append(CarriedExcerpt(block, said, cls))

    def requoted(handle: str, words: str) -> str | None:
        """`words`, quoted from `handle` (a block, or a group's members),
        as the new text reads them where a markup, maths or macro change
        ran through them (`_requoted`); unchanged when no such block of
        `handle` holds them; `None` when they cannot be carried."""
        ident = old_of.get(handle)
        if ident is None:
            return words
        members = (
            next(g for g in before.groups if g.id == ident).blocks
            if ident in old_groups
            else (ident,)
        )
        for member in members:
            link = lineage.link_of_old(member)
            if link is None or link.cls not in _REQUOTED or len(link.new) != 1:
                continue
            old_text = block_text(was, member)
            if es.norm(words) in old_text:
                return _requoted(words, old_text, block_text(version, link.new[0]))
        return words

    for link in lineage.links:
        reason = _REOPENS.get(link.cls)
        if reason is None:
            continue
        for ident in link.new:
            reopen_by(new_blocks[ident], reason, new_blocks[ident])
    # a group of `before` is the group of `after` with its members' new
    # places, else dissolved; a group only `after` holds asks its edges
    by_members = {(g.kind, g.blocks): g.id for g in after.groups}
    group_of: dict[str, str] = {}
    for group in before.groups:
        members = tuple(b for m in group.blocks for b in lineage.new_of(m))
        found = by_members.get((group.kind, members))
        if found is not None:
            group_of[group.id] = found
    for group in after.groups:
        if group.id not in group_of.values() and len(group.blocks) > 1:
            reopen_by(
                new_groups[group.id],
                "the groups made it: give its edges",
                new_groups[group.id],
            )
    # each dissolved group's handle, with the member whose change undid it
    dissolved: dict[str, str] = {}
    for ident, handle in old_groups.items():
        members = next(g for g in before.groups if g.id == ident).blocks
        if ident not in group_of and len(members) > 1:
            moved = [
                m
                for m in members
                if (x := lineage.link_of_old(m)) is None
                or x.cls not in (BlockClass.SAME, BlockClass.MOVED)
            ]
            dissolved[handle] = cause_of((moved or members)[0])

    def renamed(
        handle: str, words: str = "", said: str | None = None, lost: bool = False
    ) -> tuple[str | None, bool]:
        """A handle of `previous` as `after` names it, and whether it was
        moved from a block whose text changed; None for a deleted block
        whose words are nowhere. `said`, a line's quote as OLD read it, is
        noted with how it was carried; `lost`, it could not be re-quoted."""
        kept_as = ExcerptClass.REMOVED if lost else ExcerptClass.FOUND
        ident = old_of.get(handle)
        if ident is None:
            return handle, False  # scope:paper, file:…
        if ident in old_groups:
            group = group_of.get(ident)
            if group is not None:
                noted(new_groups[group], said, kept_as)
                return new_groups[group], False
            ident = next(g for g in before.groups if g.id == ident).blocks[0]
        link = lineage.link_of_old(ident)
        assert link is not None
        if link.cls in (BlockClass.SAME, BlockClass.MOVED, BlockClass.MARKUP):
            noted(new_blocks[link.new[0]], said, kept_as)
            return new_blocks[link.new[0]], False
        if not words:
            if said and es.norm(said):
                # a cut line follows its source; its quote is placed to say
                # how it was carried
                at = place_excerpt(said, ident, lineage, version)
                noted(new_blocks[at.block] if at.block else None, said, at.cls)
            # a whole block: its text changed is an update's to weigh; which
            # block it is now is unclear only when it split or merged
            heir = link.heir if link.heir in link.new else None
            target = heir or (link.new[0] if link.new else None)
            unclear = link.cls in (BlockClass.SPLIT, BlockClass.MERGED)
            return (new_blocks[target] if target else None), unclear
        found = place_excerpt(words, ident, lineage, version)
        noted(new_blocks[found.block] if found.block else None, said, found.cls)
        if found.block is None:
            return None, True
        handle_now = new_blocks[found.block]
        if found.cls is ExcerptClass.MOVED or link.cls is BlockClass.DELETED:
            # words of another block, or of a deleted one, found here: the
            # block that receives the line says whether it is its own
            reopen_by(
                handle_now,
                f'it receives a line of {handle}, whose words "{es.norm(words)}" '
                + ("it holds" if link.cls is BlockClass.DELETED else "moved here"),
                cause_of(ident),
            )
        if found.cls in (ExcerptClass.AMBIGUOUS, ExcerptClass.REMOVED):
            reopen_by(
                handle_now,
                f'the words "{es.norm(words)}" of {handle} are not found once',
                cause_of(ident),
            )
            if found.hint is not None:
                hints.append(
                    Hinted(
                        handle_now,
                        words,
                        found.hint.words,
                        new_blocks[found.hint.block],
                    )
                )
        return handle_now, True

    sheet = es.parse(previous)
    parts: dict[tuple[str, str], tuple[str, str, bool]] = {}
    taken: set[tuple[str, str]] = set()
    header: list[str] = []
    for (target, label), words in sheet.legend.items():
        ident = old_of[target]
        link = lineage.link_of_old(ident)
        now = requoted(target, words)
        if now is not None:
            words = now
        kept = link is not None and link.cls in (
            BlockClass.SAME,
            BlockClass.MOVED,
            BlockClass.MARKUP,
        )
        found = None if kept else place_excerpt(words, ident, lineage, version)
        said = sheet.legend[(target, label)]
        if kept:
            assert link is not None
            # placed whether re-quoted or not: the part stays on its block
            # under its label, which, reopened, quotes it anew
            new_target, placed = new_blocks[link.new[0]], True
            noted(
                new_target,
                said,
                ExcerptClass.FOUND if placed else ExcerptClass.REMOVED,
            )
            if now is None:
                # its markup changed under the words, which cannot be carried:
                # the block and every edge naming the part are asked
                reopen_by(
                    new_target,
                    f'its part [{label}]\'s words "{es.norm(words)}" cannot be '
                    "carried across its markup change: quote them as they read now",
                    new_target,
                )
        elif found is not None and found.block is not None:
            new_target = new_blocks[found.block]
            noted(new_target, said, found.cls)
            # a deleted block's part found in a neighbour is never placed:
            # the edge's source is asked
            placed = found.cls in (ExcerptClass.FOUND, ExcerptClass.MOVED) and not (
                link is not None and link.cls is BlockClass.DELETED
            )
            if not placed and _heir_of(link) == found.block:
                # lost on its one heir: the part stays there under its label,
                # unplaced, the hint beside it; the heir, reopened, quotes it
                # anew or gives it `removed`, and the edges naming it follow
                # that Δ
                placed = True
                if found.hint is not None:
                    hints.append(
                        Hinted(
                            new_target,
                            words,
                            found.hint.words,
                            new_blocks[found.hint.block],
                        )
                    )
            if placed and found.cls is ExcerptClass.MOVED:
                # as a line that moves: the block that receives it says
                # whether the part is its own
                reopen_by(
                    new_target,
                    f"it receives the part [{label}] of {target}, whose words "
                    f'"{es.norm(words)}" moved here',
                    cause_of(ident),
                )
        else:
            assert found is not None
            noted(None, said, found.cls)
            continue  # its block deleted, its words nowhere: no part
        fresh, n = label, 1
        while (new_target, fresh) in taken:
            n += 1
            fresh = f"{label}-{n}"
        taken.add((new_target, fresh))
        parts[(target, label)] = (new_target, fresh, placed)
        role = sheet.legend_roles.get((target, label))
        header.append(
            f'  {new_target} [{fresh}]: "{words}"' + (f" ({role})" if role else "")
        )
    if sheet.pervasive:
        named = dict.fromkeys(
            n for h in sheet.pervasive if (n := renamed(h)[0]) is not None
        )
        if named:
            header.append(f"Pervasive: {', '.join(named)}")

    def target_renamed(handle: str, words: str = "") -> tuple[str, bool]:
        found, moved = renamed(handle, words)
        assert found is not None  # deleted targets are dropped before
        return found, moved

    lines: dict[str, list[str]] = {}
    for owner, owned in written_lines(previous, flags=True).items():
        for line in owned:
            if es.DONE.match(line) or (cuts_weighed and line.startswith(es.CUT_MARK)):
                continue
            if line.startswith(f"{es.DELTA_MARK} "):
                continue  # how the last edit changed it: not this one's
            # the quote, and whether the line follows its words (a cut line
            # follows its source)
            quote: tuple[re.Match[str], str, bool] | None = None
            if m := es.EDGE.match(line):
                quote = m, "evidence", True
            elif m := es.SUPPORT.match(line):
                quote = m, "claim", True
            elif m := es.FLAG_LINE.match(line):
                quote = m, "evidence", True
            elif m := es.CUT.match(line):
                quote = m, "evidence", False
            words, stale, said = "", "", ""
            if quote is not None:
                m, group, follows = quote
                said = m.group(group)
                now = requoted(owner, m.group(group))
                if now is None:
                    stale = es.norm(m.group(group))
                elif now != m.group(group):
                    line = line[: m.start(group)] + now + line[m.end(group) :]
                words = (now or m.group(group)) if follows else ""
            # a cut line's point, words of the block it names
            point = ""
            if (c := es.CUT.match(line)) and c.group("at"):
                at = requoted(c.group("target") or owner, c.group("at"))
                if at is None:
                    point = es.norm(c.group("at"))
                elif at != c.group("at"):
                    line = line[: c.start("at")] + at + line[c.end("at") :]
            source, _ = renamed(owner, words, said, bool(stale))
            if source is None:
                continue  # a deleted block's line, its words nowhere
            if stale or point:
                named = owner if stale else c.group("target") or owner
                reopen_by(
                    source,
                    f'the words "{stale or point}" of {named} cannot be '
                    "carried across its markup, maths or macro change",
                    cause_of(old_of[named]) if named in old_of else source,
                )
            if owner in dissolved:
                reopen_by(
                    source,
                    f"it holds the lines of {owner}, a group now dissolved",
                    dissolved[owner],
                )
            if (m := es.EDGE.match(line)) and m.group("target"):
                target = m.group("target")
                if target in dissolved:
                    reopen_by(
                        source,
                        f"its edge onto {target}, a group now dissolved, "
                        "needs a target",
                        dissolved[target],
                    )
                labels = [
                    p.strip() for p in (m.group("parts") or "").split(",") if p.strip()
                ]
                all_placed = labels and all((target, x) in parts for x in labels)
                if not all_placed and renamed(target)[0] is None:
                    said = (
                        f"its edge onto {target}, deleted in this version, is "
                        f"gone: `{line.strip()}`"
                    )
                    # said even where another reason reopened the source
                    # first: else the writer never learns of the loss
                    if source in why:
                        why[source] += f"; {said}"
                    else:
                        reopen_by(source, said, cause_of(old_of[target]))
                    gone.append(Gone(source, line.strip(), target, old_of[target]))
                    continue
                line = _retargeted(
                    line, m, source, parts, target_renamed, why, _ACROSS, causes
                )
            elif m := es.CUT.match(line):
                target = m.group("target")
                if target:
                    found, moved = renamed(target)
                    if found is None:
                        continue
                    if moved:
                        reopen_by(
                            source,
                            f"its cut line names {target}, whose text changed",
                            found,
                        )
                    line = line[: m.start("target")] + found + line[m.end("target") :]
            lines.setdefault(source, []).append(line)
    for source, owned in lines.items():
        aims = [
            (m.group("kind"), m.group("target"))
            for line in owned
            if (m := es.EDGE.match(line)) and m.group("kind") != es.SELF
        ]
        if len(aims) != len(set(aims)):
            twice = next(a for a in aims if aims.count(a) > 1)[1]
            reopen_by(
                source,
                "two of its edges now point at one block, which a merge made",
                twice,
            )
    carried = _rendered(new, after, files, header, lines, why, reopen, tuple(hints))
    return dataclasses.replace(
        carried,
        excerpts=tuple(excerpts),
        causes={h: causes[h] for h in carried.reopened if h in causes},
        gone=tuple(gone),
    )


def stuck_cut_lines(document: Document, carried: Carried, cut: Cut) -> dict[str, str]:
    """The fixed blocks of a carried sheet whose `✂` line no longer reads on
    `cut` (a group the groups made since now lies between a merge's two
    blocks): a re-ask could neither keep nor change it, so each must be
    reopened. By handle, with why."""
    sheet = es.parse(carried.sheet)
    blocks, _ = _named_by(document, cut)
    within = {
        blocks[b.id]: blocks[b.within]
        for b in cut.blocks
        if b.within is not None and b.within in blocks
    }
    found: dict[str, str] = {}
    for line in sheet.cuts:
        if line.source in carried.reopened:
            continue
        if es._cut_problems(dataclasses.replace(sheet, cuts=[line]), within):
            found[line.source] = (
                "its cut line no longer reads on the corrected cut: correct or "
                "remove it"
            )
    return found


def reopened_by_roles(
    document: Document,
    previous: str,
    before: Cut,
    after: Cut,
    changed: Mapping[str, str],
) -> dict[str, str]:
    """What a change of roles reopens, by the type rule: for each block of `changed`
    (by id, with how its
    roles changed), the block itself, its standing, self mark, part roles
    and qualifies lines, and the source of each justifies or qualifies edge
    onto it in `previous`, the sheet written on `before`, a block or a
    group, and of each about edge onto it from a block that stands
    `restated`, which borrows the claim the change may take away
    (`docs/model.md` § 4), each by its handle on `after` and with why."""
    old_blocks, old_groups = _named_by(document, before)
    new_blocks, new_groups = _named_by(document, after)
    old_of = {h: i for i, h in old_blocks.items()} | {
        h: i for i, h in old_groups.items()
    }
    new_of = new_blocks | new_groups
    found: dict[str, str] = {}
    for ident, how in changed.items():
        if ident in new_blocks:
            found.setdefault(new_blocks[ident], f"its roles changed: {how}")
    parsed = es.parse(previous)
    restating = {s.source for s in parsed.supports if s.status == "restated"}
    for e in parsed.edges:
        target = old_of.get(e.target)
        if target not in changed:
            continue
        if e.kind not in ("justifies", "qualifies") and not (
            e.kind == "about" and e.source in restating
        ):
            continue
        source = new_of.get(old_of.get(e.source, ""))
        if source is not None:
            found.setdefault(
                source,
                f"its {e.kind} edge onto {new_blocks.get(target, e.target)}, "
                "whose roles changed",
            )
    return found


def reopened_by_groups(
    document: Document, previous: str, before: Cut, after: Cut
) -> dict[str, str]:
    """What a change of groups reopens beyond carry's group rule, by the
    type rule: a group `after` holds
    under an id `before` holds too, with other members or another kind,
    reopens itself, for its justifies edge, and the source of each about
    edge onto it in `previous`, the sheet written on `before`, each by its
    handle on `after` and with why. A group made or dissolved is carry's
    (its justifies edge, the about edges onto it). A reopened group's
    members reopen with it where they justify outside it (`_rendered`)."""
    old_blocks, old_groups = _named_by(document, before)
    new_blocks, new_groups = _named_by(document, after)
    old_of = {h: i for i, h in old_blocks.items()} | {
        h: i for i, h in old_groups.items()
    }
    was = {g.id: (g.kind, g.blocks) for g in before.groups}
    changed = {
        g.id
        for g in after.groups
        if g.id in was and was[g.id] != (g.kind, g.blocks) and len(g.blocks) > 1
    }
    found: dict[str, str] = {
        new_groups[g]: "the groups changed it: give its edges" for g in changed
    }
    for e in es.parse(previous).edges:
        if e.kind != "about" or old_of.get(e.target) not in changed:
            continue
        source = (new_blocks | new_groups).get(old_of.get(e.source, ""))
        if source is not None:
            found.setdefault(
                source,
                f"its about edge onto {new_groups[old_of[e.target]]}, whose "
                "members changed",
            )
    return found


#: A person's edges question's mark in its gate's spec:
#: no cut line and no flag.
PERSON_SPEC = "person"
#: The spec's word that its sheet may hold a person's keep mark:
#: a person's edges question's, never a model's nor an agent's.
KEEPS = "keeps"
#: The spec's problems a kept state's own sheet holds under today's check,
#: each as `problem_key` says it on the answer's handles: an answer on the
#: state is not refused for one (`docs/model.md` § 7).
KEPT_PROBLEMS = "kept_problems"
#: A problem's sheet line number, a locator rather than what is wrong.
_LINE_AT = re.compile(r"^line \d+: ")
#: What a kept sheet's block or group is named where the answer's cut does
#: not hold it: no handle starts so (`es.handles`, `es.label_handle`).
UNHELD = "†"


def problem_key(problem: str) -> str:
    """`problem` as it says what is wrong, its sheet line number set aside.
    A kept problem is matched by these words, so a check's message says
    nothing an edit elsewhere changes: no line number but its prefix, no
    count or list of other lines."""
    return _LINE_AT.sub("", problem, count=1)


def kept_problems(
    sheet: str,
    files: Sequence[str],
    *,
    onto: Mapping[str, str] | None = None,
    within: Mapping[str, str] | None = None,
    claims_fixed: Collection[str] = (),
) -> tuple[str, ...]:
    """What today's check refuses in `sheet`, a kept state's, on its own
    `files`, each as `problem_key` says it, once per time it is said. Given
    `onto`, the sheet is first renamed onto the handles of the cut an
    answer on it is given on (`es.renamed`), and a block or group `onto`
    does not take there gets a name no answer holds (`UNHELD`): a block a
    correction or a revision made is new, and holds none of them. `within`
    and `claims_fixed`, by the answer's handles, as its gate holds them.
    Empty where the state reads as kept."""
    text = sheet
    if onto is not None:
        held = es.parse(sheet).texts
        text = es.renamed(sheet, {h: onto.get(h, f"{UNHELD}{h}") for h in held})
    found = problems(
        text, text, None, files, within=dict(within or {}), claims_fixed=claims_fixed
    )
    found += group_line_problems(text)
    return tuple(sorted(problem_key(p) for p in found))


def handles_by_id(document: Document, before: Cut, after: Cut) -> dict[str, str]:
    """Each handle of `before` to the handle `after` gives the same block or
    group, by its id: two cuts of one version, a correction between them.
    A block a correction made has no handle of `before`."""
    named = {ident: h for h, ident in handles(document, after).items()}
    return {
        h: named[ident]
        for h, ident in handles(document, before).items()
        if ident in named
    }


def unkept(found: Sequence[str], kept: Collection[str]) -> list[str]:
    """`found` without the problems the kept state holds (`kept`, as
    `problem_key` says them), each standing for as many times as the kept
    sheet says it: the answer's problems."""
    left = Counter(kept)
    answered = []
    for p in found:
        if left[problem_key(p)] > 0:
            left[problem_key(p)] -= 1
        else:
            answered.append(p)
    return answered


def nested(document: Document, cut: Cut) -> dict[str, str]:
    """Each nested block's handle on `cut` to the handle of the block it
    lies in, as a gate's `within` holds them."""
    by_block = {ident: h for h, ident in handles(document, cut).items()}
    return {
        by_block[b.id]: by_block[b.within]
        for b in cut.blocks
        if b.within is not None and b.within in by_block
    }


def kept_spec(kept: Collection[str]) -> dict[str, Any]:
    """The gate's spec entry for `kept`, none where it is empty: a question
    on a sheet that reads as kept has the spec it had."""
    return {KEPT_PROBLEMS: sorted(kept)} if kept else {}


#: The spec's lines a person's or an agent's sheet shows beside its own:
#: the open stale marks, `[owner, line]`, and an update's
#: Δ entries, `[owner, label, class, note]`.
BESIDE = "beside"


@dataclass(frozen=True, slots=True)
class Beside:
    """What a kept state's sheet shows beside its lines:
    each open stale mark's line, by owner handle, and an update's Δ lines,
    one per block and part, as its record holds them now. Neither is in the
    answer's key (`person.keyed`)."""

    marks: Mapping[str, Sequence[str]] = dataclasses.field(default_factory=dict)
    deltas: Sequence[es.DeltaLine] = ()

    def __bool__(self) -> bool:
        return bool(self.marks or self.deltas)

    def spec(self) -> dict[str, Any]:
        return {
            "marks": [[o, line] for o, lines in self.marks.items() for line in lines],
            "deltas": [[d.source, d.label, d.cls, d.note] for d in self.deltas],
        }


def beside_sheet(sheet: str, beside: Beside) -> str:
    """`sheet` with each owner's Δ lines, then its stale marks' lines, just
    before its done mark."""
    if not beside:
        return sheet
    lines: dict[str, list[str]] = {}
    for d in beside.deltas:
        lines.setdefault(d.source, []).append(es.delta_line(d.label, d.cls, d.note))
    for owner, marks in beside.marks.items():
        lines.setdefault(owner, []).extend(marks)
    out: list[str] = []
    for line in sheet.split("\n"):
        if (m := es.DONE.match(line)) and m.group(1) in lines:
            out += lines[m.group(1)]
        out.append(line)
    return "\n".join(out)


def _beside_problems(spec: Mapping[str, Any], sheet: es.Sheet) -> list[str]:
    """An answer's Δ lines and stale marks against what was shown beside its
    sheet: a mark line never written; a Δ line on each shown block and part,
    once, its class or note the answer's; none added or removed."""
    beside = spec.get(BESIDE) or {}
    marks = {(o, line) for o, line in beside.get("marks", [])}
    found = [
        f"{owner}: a stale mark is the store's, shown, never written: remove `{line}`"
        for owner, line in sheet.marks
        if (owner, line) not in marks
    ]
    shown = {(o, label) for o, label, _, _ in beside.get("deltas", [])}
    given = [(d.source, d.label) for d in sheet.deltas]
    seen: set[tuple[str, str]] = set()
    for d in sheet.deltas:
        named = f" [{d.label}]" if d.label else ""
        if (d.source, d.label) in seen and shown:
            found.append(
                f"line {d.line}: {d.source} Δ{named}: a second Δ line on one block "
                "and part: keep one, its class the one you read"
            )
        seen.add((d.source, d.label))
        if (d.source, d.label) not in shown:
            found.append(
                f"line {d.line}: {d.source} Δ{named}: a Δ line corrects one the "
                "sheet shows, on the same block and part; add none"
                if shown
                else f"line {d.line}: {d.source} Δ: a Δ line is an update's, not "
                "this pass's; remove it"
            )
    for owner, label in sorted(shown - set(given)):
        named = f" [{label}]" if label else ""
        found.append(
            f"{owner} Δ{named}: a shown Δ line is corrected, never removed: put it "
            "back, its class the one you read"
        )
    return found


class ExportLines:
    """A kept export's edge, self mark and standing lines as the edges
    sheet writes them under their owners, each block or group named by
    `name`: one writer for the sheet a state shows (`state_sheet`) and
    for `show`."""

    def __init__(self, export: Mapping[str, Any], name: Callable[[str], str]) -> None:
        self.name = name
        self.standings: dict[str, list[Mapping[str, Any]]] = {}
        for s in export.get("standings", []):
            self.standings.setdefault(s["block"], []).append(s)

    def claim_handles(self, ident: str) -> dict[str, str]:
        """Each claim's words on block `ident`, to its handle as the edges
        sheet names it: the one its ⊢ line defines, else minted from its
        words, a numeric suffix where it would repeat one
        (`edge_sheet.standings_by_handle`)."""
        held = self.standings.get(ident, [])
        taken = {s["handle"] for s in held if s.get("handle")}
        found: dict[str, str] = {}
        for s in held:
            handle = s.get("handle")
            if not handle:
                minted = handle = es.handle_for(s["claim"]["text"])
                n = 1
                while handle in taken:
                    n += 1
                    handle = f"{minted}-{n}"
                taken.add(handle)
            found.setdefault(s["claim"]["text"], handle)
        return found

    def _named(self, block: str | None, claims: Sequence[str]) -> list[str]:
        held = self.claim_handles(block) if block else {}
        return [held.get(c, f'"{c}"') for c in claims]

    def target(self, edge: Mapping[str, Any]) -> tuple[str, list[str]]:
        """An edge's target as the sheet names it, and its parts' labels."""
        t = edge["target"]
        if "block" in t:
            return self.name(t["block"]), [entry_label(e) for e in t.get("entries", [])]
        if "group" in t:
            return self.name(t["group"]), []
        if "file" in t:
            return f"{es.FILE}{t['file']}", []
        return es.SCOPE_PAPER, []

    def edge_line(self, edge: Mapping[str, Any]) -> str:
        """An edge as the sheet holds it under its source: the source end's
        claims after its kind, the target's claims among its parts."""
        target, parts = self.target(edge)
        own = self._named(edge["source"], edge.get("source_claims", ())) + (
            [es.RESTATES_NONE] if edge.get("restates_none") else []
        )
        parts += self._named(
            edge["target"].get("block"), edge["target"].get("claims", ())
        )
        bits = [f"→ {edge['kind']}" + (f" [{', '.join(own)}]" if own else "")]
        bits.append(target)
        if parts:
            bits.append(f"[{', '.join(parts)}]")
        if edge.get("mark"):
            bits.append(edge["mark"])
        if edge.get("where_used"):
            bits.append("where-used")
        if edge.get("generalizes"):
            bits.append("generalizes")
        if edge.get("assumed"):
            bits.append("assumed")
        if edge.get("partial"):
            bits.append(edge["partial"])
        return " ".join(bits) + _quoted(edge) + _note(edge)

    def self_line(self, mark: Mapping[str, Any]) -> str:
        """A self mark, the block's claims it supports by handle."""
        own = self._named(mark["block"], mark.get("claims", ()))
        return (
            "→ self"
            + (f" [{', '.join(own)}]" if own else "")
            + (f" {mark['partial']}" if mark.get("partial") else "")
            + _quoted(mark)
            + _note(mark)
        )

    @staticmethod
    def standing_line(standing: Mapping[str, Any]) -> str:
        """A standing as the edges sheet shows it: its claim's handle where
        the sheet shows one (`edge_sheet.shown_handles`), which the export
        keeps."""
        handle = f" [{standing['handle']}]" if standing.get("handle") else ""
        declared = f" {standing['declared']}" if standing.get("declared") else ""
        return (
            f"⊢ {standing['status']}{declared}{handle} · "
            f'"{standing["claim"]["text"]}"' + _note(standing)
        )


def _quoted(item: Mapping[str, Any]) -> str:
    evidence = item.get("evidence") or [{"text": ""}]
    return f' · "{evidence[0]["text"]}"'


def _note(item: Mapping[str, Any]) -> str:
    return f" — {item['note']}" if item.get("note") else ""


#: A sheet's `✂` line's operation, by the correction's (a rebound is a
#: `move`).
_CUT_LINE_OPS = {CutOp.SPLIT: "split", CutOp.MERGE: "merge", CutOp.REBOUND: "move"}


def state_sheet(
    document: Document,
    cut: Cut,
    files: Sequence[str],
    export: Mapping[str, Any],
    orders: Sequence[LineOrder] = (),
    cuts: Sequence[CutCorrection] = (),
) -> str:
    """The edges sheet a kept state shows on its cut, rendered from its
    export (`docs/architecture.md` § 4): the cut's skeleton and handles,
    the legend from its entries, a part's role where it is not its
    segment's, the `Pervasive:` line from its ambient blocks, and under
    each owner its edges, self marks and standings, in the order `orders`
    gives where kinds interleave, then `cuts` as `✂` lines, then its done
    mark. What the carried answer's sheet says on the same cut, line for
    line."""
    blocks, groups = _named_by(document, cut)
    by_id = blocks | groups
    lines = ExportLines(export, lambda ident: by_id.get(ident, ident))
    skeleton = es.render(
        sheet_blocks(document, cut), sheet_groups(document, cut), files, HEADER
    )
    shown = es.parse(skeleton)
    legend = []
    for entry in export.get("entries", []):
        found = es.segments(shown, entry["id"].split(" [", 1)[0])
        at = es.segment_role_at(found, es.norm(entry["anchor"])) or found[0][0]
        role = entry.get("role")
        named = f" ({role})" if role not in (None, es.NONE, at) else ""
        legend.append(f'  {entry["id"]}: "{entry["anchor"]}"{named}')
    ambient = [lines.name(b) for b in export.get("ambient", [])]
    header = [*legend, *([f"Pervasive: {', '.join(ambient)}"] if ambient else [])]
    owned: dict[str, dict[LineKind, list[str]]] = {}

    def put(ident: str, kind: LineKind, line: str) -> None:
        owned.setdefault(lines.name(ident), {}).setdefault(kind, []).append(line)

    for e in export.get("edges", []):
        put(e["source"], LineKind.EDGE, lines.edge_line(e))
    for m in export.get("self") or []:
        put(m["block"], LineKind.SELF, lines.self_line(m))
    for s in export.get("standings", []):
        put(s["block"], LineKind.STANDING, lines.standing_line(s))
    ordered = {lines.name(o.owner): o.kinds for o in orders}
    cut_lines: dict[str, list[str]] = {}
    for c in cuts:
        owner_of = c.block if c.block is not None else (c.blocks or (None,))[0]
        if owner_of is None or c.op not in _CUT_LINE_OPS:
            continue
        target = f" {lines.name(c.blocks[1])}" if c.op is not CutOp.SPLIT else ""
        tail = f' · "{c.evidence[0] if c.evidence else ""}"' + (
            f" — {c.why}" if c.why else ""
        )
        cut_lines.setdefault(lines.name(owner_of), []).extend(
            f"{es.CUT_MARK}{_CUT_LINE_OPS[c.op]}{target}"
            + "".join(f' "{w}"' for w in ats)
            + tail
            for ats in ([[w] for w in c.at] if c.op is CutOp.SPLIT else [list(c.at)])
        )

    def owner_lines(owner: str) -> list[str]:
        mine = owned.get(owner, {})
        held = {k: iter(v) for k, v in mine.items()}
        rank = [k for k in _KIND_RANK for _ in mine.get(k, ())]
        kinds = ordered.get(owner) or rank
        # an order is named by block id on the answer's cut and read on the
        # state's: where its count of each kind is not the owner's lines',
        # it is another block's order, and the default rank stands
        if sorted(kinds, key=_KIND_RANK.__getitem__) != rank:
            kinds = rank
        return [next(held[k]) for k in kinds] + cut_lines.get(owner, [])

    out: list[str] = []
    owner: str | None = None

    def close() -> None:
        if owner is not None:
            out.extend([*owner_lines(owner), f"⟦done {owner}⟧", ""])

    for line in skeleton.split("\n"):
        if line.startswith("Files: "):
            out.extend(header)
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            close()
            owner = None if es.GROUP.match(line) else m.group(1)
        out.append(line)
    close()
    return es.with_handles("\n".join(out))


def person_sheet(
    document: Document, previous: str, before: Cut, after: Cut, files: Sequence[str]
) -> str:
    """The edges a person edits: `previous`, the last
    edges answer's sheet, written on `before`, carried onto `after`, the
    final cut, nothing reopened, without its cut lines and flag lines (a
    person corrects the cut in cut.md, and a flag asks a later step) and
    without an update's Δ, ⟦was⟧ and condition lines, which carry never
    takes."""
    carried = carry(document, previous, before, after, files)
    return "\n".join(
        line
        for line in carried.sheet.split("\n")
        if not line.startswith(f"{es.FLAG_MARK} ")
    )


def person_question(
    document: Document,
    cut: Cut,
    template: Template,
    files: Sequence[str],
    sheet: str,
    *,
    keeps: bool = True,
    pins: Sequence[tuple[str, str, str]] = (),
    beside: Beside | None = None,
    kept: Collection[str] = (),
) -> Question:
    """The edges question a person answers: the whole
    paper's sheet as `person_sheet` shows it, every owner open, checked as
    a writer's sheet is, and refusing a cut line, a `?` flag and a Δ line;
    never for a problem the kept state's sheet holds (`kept`,
    `kept_problems`).
    An agent's is the same question, but refusing the keep mark
    (`keeps`) and holding a person's pins (`pins`, as `person.gate_pins`
    gives them). A kept state's shows `beside` its
    lines: its open stale marks and an update's Δ lines, which the answer
    may correct (`_beside_problems`). Its key is the person's
    (`person.person_key`), set by the caller."""
    return _question(
        document,
        cut,
        template,
        files,
        None,
        shown=beside_sheet(sheet, beside) if beside else sheet,
        instructions=template.render(),
        spec_extra={
            PERSON_SPEC: True,
            **({KEEPS: True} if keeps else {}),
            **_pinned(pins),
            **({BESIDE: beside.spec()} if beside else {}),
            **kept_spec(kept),
        },
    )


#: Why `_retargeted` reopens a source, within one version.
_BY_CORRECTION = (
    "needs a target: a cut correction changed that block",
    "names parts a cut correction split between blocks or cut through",
)
#: ... and across two.
_ACROSS = (
    "needs a target: that block was split or merged in this version",
    "names parts whose words are not found once in this version",
)


def _retargeted(
    line: str,
    m: re.Match[str],
    source: str,
    parts: dict[tuple[str, str], tuple[str, str, bool]],
    renamed: Callable[..., tuple[str, bool]],
    why: dict[str, str],
    reasons: tuple[str, str] = _BY_CORRECTION,
    causes: dict[str, str] | None = None,
) -> str:
    """An edge line with its target and parts as the new cut names them. A
    whole-block target that changed, or parts that a correction split
    between blocks or cut through, reopen `source`; across versions, the
    target as now named is noted in `causes` as where the reopen comes
    from."""

    def reopen(reason: str, cause: str) -> None:
        if source not in why:
            why[source] = reason
            if causes is not None:
                causes[source] = cause

    target = m.group("target") or ""
    if not target:
        return line
    labels = [p.strip() for p in (m.group("parts") or "").split(",") if p.strip()]
    placed = [parts[(target, label)] for label in labels if (target, label) in parts]
    if labels and len(placed) == len(labels):
        new = placed[0][0]
        if len({p[0] for p in placed}) > 1 or not all(p[2] for p in placed):
            reopen(f"its edge onto {target} {reasons[1]}", new)
        return (
            line[: m.start("target")]
            + new
            + line[m.end("target") : m.start("parts")]
            + ", ".join(p[1] for p in placed)
            + line[m.end("parts") :]
        )
    new, moved = renamed(target)
    if moved:
        reopen(f"its edge onto {target} {reasons[0]}", new)
    return line[: m.start("target")] + new + line[m.end("target") :]


def reask_question(
    document: Document,
    cut: Cut,
    writer: Template,
    reask: Template,
    files: Sequence[str],
    carried: Carried,
    round_: int,
    corrections: Sequence[str],
    limit: int | None = None,
    pins: Sequence[tuple[str, str, str]] = (),
    claims_fixed: Collection[str] = (),
    kept: Collection[str] = (),
) -> Question:
    """The edges again after a round of cut corrections: a
    fresh session on the sheet carried onto the corrected cut, under the
    writer's brief, then the re-ask's, then the corrections applied
    (`corrections`, one line each) and why each reopened block is open.
    The lines of every block not reopened are fixed. The key adds the
    round, the sheet shown, the reopened blocks with their reasons, the
    corrections and the writer's brief. `pins`, a person's lines, each
    `(owner, line, kind)` on this sheet, hold in a reopened owner too:
    bound in the key and the gate only where there are any;
    `claims_fixed`, the blocks whose roles a person gave (`_question`);
    `kept`, the problems the sheet held before the round (`kept_problems`),
    which the gate does not refuse."""
    notes = "\n".join(
        [
            "### The corrections applied",
            "",
            *(f"- {line}" for line in corrections),
            "",
            "### The blocks reopened",
            "",
            *(f"- {h}: {carried.why[h]}" for h in carried.reopened),
        ]
    )
    return _question(
        document,
        cut,
        reask,
        files,
        limit,
        shown=carried.sheet,
        reopened=carried.reopened,
        instructions=f"{writer.render()}\n\n{reask.render()}\n\n{notes}\n",
        prompt=REASK_KICKOFF,
        key_extra={
            "round": round_,
            "shown": hashlib.sha256(carried.sheet.encode("utf-8")).hexdigest(),
            "reopened": [[h, carried.why[h]] for h in carried.reopened],
            "corrections": list(corrections),
            "writer": writer.version,
            **_pinned(pins),
        },
        spec_extra={**_pinned(pins), **kept_spec(kept)},
        claims_fixed=claims_fixed,
    )


def _pinned(pins: Sequence[tuple[str, str, str]]) -> dict[str, Any]:
    """A question's pins as its key and its gate's spec bind them: only
    where there are any, so every key without pins stands."""
    return {"pins": [list(p) for p in pins]} if pins else {}


#: A pin's kinds on the edges sheet: a line a person
#: wrote, and one a person deleted; a line a person kept as it stands,
#: by the keep mark, holding as a written one does.
WRITTEN_PIN = "written"
DELETED_PIN = "deleted"
KEPT_PIN = "kept"
#: The kinds whose line stands under its owner.
STANDS = (WRITTEN_PIN, KEPT_PIN)
#: A deleted block's name where a reopen comes from it: it has no block of
#: NEW (`update.DELETED`).
DELETED_BLOCK = "deleted:"


#: The edges check's problem that names two lines against each other: one
#: source, kind and target twice (`edge_sheet.check`).
DUPLICATE = "a second edge for one source, kind and target"


def implicated(problem: str, pins: Sequence[Sequence[str]]) -> list[tuple[int, str]]:
    """The written pins `(owner, line, kind)` a problem of the edges check
    names, by index, each with the check as it reads past the line it
    names. An edge's problem names the pins of its
    owner, kind and target, and, where the problem is about the line's own
    content, only those whose line holds it: its evidence (empty, or not
    in its owner, by the words quoted), a part it names, its `where-used`,
    `generalizes`, `assumed` or `optional` flag. A standing's names those
    of its owner and status, its claim's words likewise. A part's legend
    line names the pins whose edge names that part. Deterministic: the
    same problems name the same pins on replay."""
    said = re.sub(r"^line \d+: ", "", problem)
    found = []
    for n, (owner, line, kind) in enumerate(pins):
        if kind not in STANDS:
            continue
        heads: list[str] = []
        holds: Callable[[str], bool] = lambda rest: True  # noqa: E731
        if m := es.EDGE.match(line):
            target = m.group("target") or ""
            labels = [
                x.strip() for x in (m.group("parts") or "").split(",") if x.strip()
            ]
            heads.append(f"{owner} → {m.group('kind')} {target}".rstrip() + ": ")
            heads += [f"part {target} [{label}]: " for label in labels]
            holds = _edge_holds(m, labels)
        elif m := es.SUPPORT.match(line):
            heads.append(f"{owner} ⊢ {m.group('status')}: ")
            holds = _standing_holds(m)
        head = next((h for h in heads if said.startswith(h)), None)
        if head is None:
            continue
        rest = said.removeprefix(head)
        if head.startswith("part ") or holds(rest):
            found.append((n, rest.split(": ")[0]))
    return found


def _edge_holds(m: re.Match[str], labels: Sequence[str]) -> Callable[[str], bool]:
    """Whether the rest of an edge's problem is about this line's content,
    where it is about a line's content at all."""
    evidence, flag = m.group("evidence"), m.group("flag") or ""

    def holds(rest: str) -> bool:
        if rest.startswith("evidence not in "):
            return rest.endswith(repr(evidence)) or f": {evidence!r}" in rest
        if rest.startswith("the evidence is empty"):
            return not es.norm(evidence)
        if rest.startswith("no part ["):
            return any(rest.startswith(f"no part [{label}]") for label in labels)
        if rest.startswith("where-used"):
            return "where-used" in flag
        if rest.startswith("generalizes"):
            return "generalizes" in flag
        if rest.startswith("assumed"):
            return "assumed" in flag
        if rest.startswith("`optional`"):
            return "optional" in flag
        # two justifies lines from one source onto one target: a partial's
        # problem is the flagged line's
        if rest.startswith("an analogy names"):
            return "analogy" in flag
        if rest.startswith(
            ("one of sketch or analogy", "sketch marks", "analogy marks")
        ):
            return "sketch" in flag or "analogy" in flag
        return True

    return holds


def _standing_holds(m: re.Match[str]) -> Callable[[str], bool]:
    claim = m.group("claim")

    def holds(rest: str) -> bool:
        if rest.startswith("claim not in "):
            return f": {claim!r}" in rest
        if rest.startswith("the claim's words are empty"):
            return not es.norm(claim)
        return True

    return holds


def pin_problems(
    pins: Sequence[Sequence[str]],
    edited: str,
    structural: Sequence[str] = (),
    shown: str | None = None,
) -> list[str]:
    """A person's pinned lines against an edited sheet:
    each written one stands under its owner as written, and no deleted one
    is back; each of the sheet's `structural` problems a pinned line takes
    part in names the pin, as an answer never lapses one (an answer that
    drops the edge a pinned standing rests on is refused); and what a
    written pin means holds with it: each part a pinned edge names keeps
    its legend line (words and role) as `shown` has it, and a pinned
    standing's claim takes no second standing. Only a person's later
    answer changes them."""
    found = [
        f"{pins[n][0]}: this breaks the person's pinned line `{pins[n][1].strip()}` "
        f"({why}): keep what it rests on"
        for problem in structural
        for n, why in implicated(problem, pins)
    ]
    lines = {
        owner: [line.strip() for line in owned]
        for owner, owned in written_lines(edited).items()
    }
    for owner, line, kind in pins:
        held = line.strip() in lines.get(owner, [])
        if kind in STANDS and not held:
            found.append(
                f"{owner}: the line `{line.strip()}` is a person's, pinned: keep it "
                "as written"
            )
        elif kind == DELETED_PIN and held:
            found.append(
                f"{owner}: the line `{line.strip()}` was deleted by a person, and is "
                "pinned: remove it"
            )
    if shown is not None:
        found += _meaning_problems(pins, es.parse(shown), es.parse(edited))
    return found


def group_line_problems(
    edited: str,
    editable: Collection[str] | None = None,
    pins: Sequence[Sequence[str]] = (),
) -> list[str]:
    """Each justifies line of a group's member (a block of it, or a group
    nested in it) onto a target the group's own justifies line names, the
    claims of the two meeting (or either naming none): the group is the
    one run that argues it (docs/model.md § 3), so its line carries its
    members'. Only a line the answer may change (its owner in `editable`,
    every owner where None), and never a person's: pinned standing
    (`pins`) or kept by the keep mark. One wording for every sheet, so a
    kept state's problem matches an answer's (`kept_problems`)."""
    sheet = es.parse(edited)
    held = {(owner, line.strip()) for owner, line, kind in pins if kind in STANDS}
    held |= {(owner, line.strip()) for owner, line in sheet.kept}
    texts = edited.split("\n")
    blocks = {g: set(m) for g, m in sheet.groups.items()}
    found = []
    for group, members in blocks.items():
        argued = [e for e in sheet.edges if e.source == group and e.kind == "justifies"]
        if not argued:
            continue
        nested = {g for g, m in blocks.items() if g != group and m <= members}
        for e in sheet.edges:
            if e.kind != "justifies" or e.source not in members | nested:
                continue
            if editable is not None and e.source not in editable:
                continue
            if (e.source, es.unkept(texts[e.line - 1])[0].strip()) in held:
                continue
            if any(
                g.target == e.target
                and (not g.parts or not e.parts or set(g.parts) & set(e.parts))
                for g in argued
            ):
                found.append(
                    f"line {e.line}: {e.source} → justifies {e.target}: its group "
                    f"{group} justifies {e.target} too, and the group's line carries "
                    "its members': remove this one (a person keeps it with the keep "
                    "mark)"
                )
    return found


def _meaning_problems(
    pins: Sequence[Sequence[str]], shown: es.Sheet, edited: es.Sheet
) -> list[str]:
    """What an answer changed of a written pin's meaning: the legend line
    of a part a pinned edge names, or a second standing on a pinned
    standing's claim."""
    found = []
    for owner, line, kind in pins:
        if kind not in STANDS:
            continue
        if m := es.EDGE.match(line):
            target = m.group("target") or ""
            for label in (x.strip() for x in (m.group("parts") or "").split(",")):
                key = (target, label)
                if not label or key not in shown.legend:
                    continue
                if (shown.legend[key], shown.legend_roles.get(key)) != (
                    edited.legend.get(key),
                    edited.legend_roles.get(key),
                ):
                    found.append(
                        f"part {target} [{label}]: a person's pinned line "
                        f"`{line.strip()}` names it: keep its legend line as shown"
                    )
        elif m := es.SUPPORT.match(line):
            claim = es.norm(m.group("claim"))
            same = [
                x
                for x in edited.supports
                if x.source == owner and es.norm(x.claim) == claim
            ]
            if len(same) > 1:
                found.append(
                    f"{owner}: a second standing on the claim of the person's pinned "
                    f"line `{line.strip()}`: remove it"
                )
    return found


# --- an update --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Placed:
    """A condition line and the edge it stands under: the line of `owner`
    whose kind, target and parts are the edge's, else first among the
    owner's lines (the ambient frame has no line)."""

    owner: str
    kind: str
    target: str
    parts: tuple[str, ...]
    text: str


@dataclass(frozen=True, slots=True)
class SheetLines:
    """What of a carried sheet is not its cut rendered: the files its
    header shows, its legend and pervasive lines, and each block's and
    group's own lines (`written_lines` with flags) but their done marks,
    which the reopened owners give."""

    files: tuple[str, ...]
    header: tuple[str, ...]
    lines: Mapping[str, tuple[str, ...]]

    def data(self) -> dict[str, Any]:
        return {
            # a code file with its opening line, as its `Code:` line shows it
            "files": [
                [str(f), f.opening] if isinstance(f, es.CodeFile) else str(f)
                for f in self.files
            ],
            "header": list(self.header),
            "lines": {o: list(x) for o, x in self.lines.items()},
        }

    @classmethod
    def of(cls, data: Mapping[str, Any]) -> SheetLines:
        return cls(
            tuple(f if isinstance(f, str) else es.CodeFile(*f) for f in data["files"]),
            tuple(data["header"]),
            {o: tuple(x) for o, x in data["lines"].items()},
        )


def sheet_lines(sheet: str) -> SheetLines:
    """A carried sheet's own lines (`SheetLines`)."""
    parsed = es.parse(sheet)
    header = [
        f'  {target} [{label}]: "{words}"'
        + (f" ({role})" if (role := parsed.legend_roles.get((target, label))) else "")
        for (target, label), words in parsed.legend.items()
    ]
    if parsed.pervasive:
        header.append(f"Pervasive: {', '.join(parsed.pervasive)}")
    return SheetLines(
        tuple(parsed.files or ()),
        tuple(header),
        {
            owner: tuple(line for line in owned if not es.DONE.match(line))
            for owner, owned in written_lines(sheet, flags=True).items()
        },
    )


def lines_sheet(
    document: Document, cut: Cut, held: SheetLines, why: Mapping[str, str]
) -> str:
    """The sheet `held`'s lines show on `cut`, a done mark on each owner
    not reopened (`why`), rendered by today's code: `sheet_lines` read
    back."""
    lines = {o: list(x) for o, x in held.lines.items()}
    return _rendered(
        document, cut, held.files, list(held.header), lines, dict(why), {}
    ).sheet


def update_sheet(
    document: Document,
    cut: Cut,
    files: Sequence[str],
    carried: Carried,
    opened: Mapping[str, str],
    was: Mapping[str, Sequence[str]],
    conditions: Sequence[Placed],
) -> Carried:
    """The sheet an update shows: `carried`, the sheet carried
    onto the new version's cut, with the done marks of the owners `opened`
    (by handle, with why) taken off, each changed block's old texts under
    its line (`was`), and each condition line under its reaching edge.
    Every other owner keeps its lines and done mark verbatim."""
    held = sheet_lines(carried.sheet)
    header, lines = list(held.header), {o: list(x) for o, x in held.lines.items()}
    for c in conditions:
        owned = lines.setdefault(c.owner, [])
        at = next(
            (
                n + 1
                for n, line in enumerate(owned)
                if (m := es.EDGE.match(line))
                and (m.group("kind"), m.group("target") or "") == (c.kind, c.target)
                and tuple(
                    p.strip() for p in (m.group("parts") or "").split(",") if p.strip()
                )
                == c.parts
            ),
            0,
        )
        while at < len(owned) and es.CONDITION.match(owned[at]):
            at += 1  # after the conditions already under that line
        owned.insert(at, c.text)
    why = {h: carried.why[h] for h in carried.reopened} | dict(opened)
    return _rendered(document, cut, files, header, lines, why, {}, carried.hints, was)


def reopened_with(
    sheet: Carried, reopened: Mapping[str, str], conditional: Collection[str]
) -> dict[str, str]:
    """An update's reopened owners, by handle with why, and those its sheet
    (`update_sheet`) reopened besides, a reopened group's members
    (`_rendered`), but the owners open on condition: what its gate holds
    open."""
    return dict(reopened) | {
        h: sheet.why[h]
        for h in sheet.reopened
        if h not in reopened and h not in conditional
    }


def structure(
    document: Document,
    cut: Cut,
    files: Sequence[str],
    claims_fixed: Collection[str] = (),
) -> dict[str, Any]:
    """What the edges gate checks a sheet on `cut` against, as a question's
    spec holds it: the sheet rendered, the blocks' ids, the paper's files
    and the nested blocks' outer ones, by handle; and, where any, the
    blocks of `claims_fixed` (by id) whose roles a person pinned."""
    blocks = sheet_blocks(document, cut)
    by_block = es.block_handles(blocks)
    fixed = sorted(by_block[b] for b in claims_fixed if b in by_block)
    return {
        **({CLAIMS_FIXED: fixed} if fixed else {}),
        "reference": es.render(blocks, sheet_groups(document, cut), files, HEADER),
        "block_ids": [b.id for b in cut.blocks],
        "files": list(files),
        "within": {
            by_block[b.id]: by_block[b.within]
            for b in cut.blocks
            if b.within is not None and b.within in by_block
        },
    }


def unreadable(
    document: Document,
    cut: Cut,
    files: Sequence[str],
    sheet: str,
    open_: Collection[str],
    claims_fixed: Collection[str] = (),
    gone: Collection[tuple[str, str]] = (),
    kept: Collection[str] = (),
) -> dict[str, str]:
    """The owners of `sheet`, written on `cut` and not in `open_`, whose
    own lines the edges check refuses there,
    each with why: a gate that fixed them could never pass, so they are
    opened before a session is asked. A problem of an edge or standing
    names its owner; a part's legend line, its block and the owners of the
    edges that name it, but a part in `gone`, its block's alone: the writer
    quotes it anew or gives it `removed`, and the edges follow that Δ. What is merely
    unfinished names nobody, nor does a problem the kept state held
    (`kept`, `kept_problems`), which the gate does not refuse.
    `claims_fixed`, by id, the blocks whose roles a person pinned, as the
    gate holds them."""
    spec = structure(document, cut, files, claims_fixed)
    parsed = es.parse(sheet)
    found: dict[str, str] = {}
    refused = problems(
        spec["reference"],
        sheet,
        spec["block_ids"],
        spec["files"],
        within=spec["within"],
        claims_fixed=spec.get(CLAIMS_FIXED, ()),
    )
    for problem in unkept(refused, kept):
        said = problem_key(problem)
        if said.startswith(es.UNFINISHED):
            continue
        owners: list[str] = []
        if m := re.match(r"^(\S+) (?:→|⊢) ", said):
            owners = [m.group(1)]
        elif m := re.match(r"^part (\S+) \[([^\]]+)\]: ", said):
            owners = [m.group(1)]
            if (m.group(1), m.group(2)) not in gone:
                owners += [
                    e.source
                    for e in parsed.edges
                    if e.target == m.group(1) and m.group(2) in e.parts
                ]
        for owner in owners:
            if owner in parsed.texts and owner not in open_:
                found.setdefault(
                    owner, f"its lines no longer read on this version: {said}"
                )
    return found


def sheet_problems(
    document: Document,
    cut: Cut,
    files: Sequence[str],
    sheet: str,
    claims_fixed: Collection[str] = (),
    kept: Collection[str] = (),
) -> list[str]:
    """What the edges gate finds wrong with `sheet` as an answer on `cut`,
    with nothing fixed: an update that opens nothing exports a carried
    sheet only where it reads, a problem the kept state held (`kept`)
    aside; `claims_fixed` as `unreadable`'s."""
    found = structure(document, cut, files, claims_fixed)
    return unkept(
        problems(
            found["reference"],
            sheet,
            found["block_ids"],
            found["files"],
            within=found["within"],
            claims_fixed=found.get(CLAIMS_FIXED, ()),
        ),
        kept,
    )


def update_question(
    document: Document,
    cut: Cut,
    writer: Template,
    update: Template,
    files: Sequence[str],
    sheet: str,
    *,
    session: int,
    reopened: Mapping[str, str],
    conditional: Mapping[str, Sequence[str]],
    owed: Mapping[str, Sequence[str | None]],
    graph: EdgeGraph,
    rule_deltas: Sequence[tuple[str, str | None, str]],
    identity: str,
    hints: Sequence[Hinted] = (),
    limit: int | None = None,
    gone: Collection[tuple[str, str]] = (),
    roots: Sequence[str] | None = None,
    earlier: Collection[str] | None = None,
    pins: Sequence[tuple[str, str, str]] = (),
    claims_fixed: Collection[str] = (),
    kept: Collection[str] = (),
) -> Question:
    """An update's session: a fresh session on `sheet`
    (`update_sheet`'s), under the writer's brief, then the update's, then
    the Δ lines owed (`owed`: each changed block's parts, None for the
    block whole), the owners reopened with why, the owners open on
    condition with their conditions, and the fuzzy hints. Its gate reads
    the shown graph (`graph`, by handle) and the rule's Δs (`rule_deltas`,
    a deleted block's `removed`), runs `closure.impact_reach` on them and
    the writer's Δ lines from `roots` (the owed blocks when None), and
    fixes every owner neither reopened nor opened so. `gone` names the owed
    parts whose words the edit removed: their legend lines are shown as
    carried, to be deleted, and their Δ is `removed`. The key adds the
    session, the sheet shown, the reopened owners with why, the conditional
    ones with their conditions, the identity answer's key and the writer's
    brief.

    A follow-up session owes no text Δ and names
    `earlier`, the owners an earlier session left open: the walk opens
    none of them, and their lines, Δ lines among them, stand as shown.
    `pins`, a person's lines carried onto this sheet, hold as in a re-ask
    (`pin_problems`); `kept`, the problems OLD's sheet held
    (`kept_problems`), are not refused."""
    if session == 1 or owed:
        notes = [
            "### The Δ lines owed",
            "",
            *(
                f"- {h}: "
                + (
                    ", ".join(
                        f"[{p}]" + (" (its words are gone)" if (h, p) in gone else "")
                        for p in parts
                        if p is not None
                    )
                    if any(p is not None for p in parts)
                    else "one for the block"
                )
                for h, parts in owed.items()
            ),
        ]
    else:
        notes = [FOLLOWUP_NOTE]
    notes += [
        "",
        "### The blocks reopened",
        "",
        *(f"- {h}: {why}" for h, why in reopened.items()),
    ]
    if conditional:
        notes += [
            "",
            "### The blocks open on condition",
            "",
            *(f"- {h}: {'; '.join(c)}" for h, c in conditional.items()),
        ]
    if hints:
        notes += [
            "",
            "### Hints",
            "",
            "Words a line quoted that are not found once in this version, and "
            "where something like them stands. A hint is never written for you: "
            "quote the paper as it reads now.",
            "",
            *(
                f'- {h.handle}: "{es.norm(h.excerpt)}", perhaps now "{h.words}" '
                f"in {h.block}"
                for h in hints
            ),
        ]
    roots = list(owed) if roots is None else list(roots)
    return _question(
        document,
        cut,
        update,
        files,
        limit,
        shown=sheet,
        reopened=tuple(reopened),
        instructions=f"{writer.render()}\n\n{update.render()}\n\n"
        + "\n".join(notes)
        + "\n",
        prompt=UPDATE_KICKOFF,
        key_extra={
            "session": session,
            "shown": hashlib.sha256(sheet.encode("utf-8")).hexdigest(),
            "reopened": [[h, why] for h, why in reopened.items()],
            "conditional": [[h, list(c)] for h, c in conditional.items()],
            "identity": identity,
            "writer": writer.version,
            **_pinned(pins),
        },
        spec_extra={
            **_pinned(pins),
            **kept_spec(kept),
            "update": {
                "conditional": sorted(conditional),
                "roots": roots,
                "owed": {h: [p or "" for p in parts] for h, parts in owed.items()},
                "graph": graph_payload(graph),
                "rule_deltas": [[h, e or "", c] for h, e, c in rule_deltas],
                "gone": [[h, label] for h, label in sorted(gone)],
                **({"earlier": sorted(earlier)} if earlier is not None else {}),
            },
        },
        claims_fixed=claims_fixed,
    )


def graph_payload(graph: EdgeGraph) -> dict[str, Any]:
    """The rule's graph as a gate's spec holds it."""
    return {
        "blocks": list(graph.blocks),
        "groups": {g: list(m) for g, m in graph.groups.items()},
        "links": [
            [e.source, e.kind, e.target, e.content, e.where_used, list(e.parts)]
            for e in graph.links
        ],
        "ambient": sorted(graph.ambient),
    }


def graph_from_payload(payload: Mapping[str, Any]) -> EdgeGraph:
    """The rule's graph from a gate's spec (`graph_payload`)."""
    return EdgeGraph(
        blocks=tuple(payload["blocks"]),
        groups={g: tuple(m) for g, m in payload["groups"].items()},
        links=tuple(
            ClosureLink(source, kind, target, content, where_used, tuple(parts))
            for source, kind, target, content, where_used, parts in payload["links"]
        ),
        ambient=frozenset(payload["ambient"]),
    )


#: What a follow-up session is told in place of the Δ lines owed.
FOLLOWUP_NOTE = """### A follow-up session

A session before this one wrote the update's Δ lines and checked the
blocks it opened; their lines, its Δ lines among them, stand under a done
mark and are fixed. What it wrote reaches the blocks open below: a Δ on a
block whose text did not change, an edge it wrote or dropped, or a new
part's words another block writes. No changed block's old text is shown,
and no Δ line is owed but a block's whose meaning moved by rule."""


def dropped_part_owners(
    shown: es.Sheet,
    sheet: es.Sheet,
    opened: Collection[str],
    closed: Callable[[str], bool],
) -> dict[str, tuple[str, str]]:
    """The mirror check: for a uses edge B → A naming parts, shown from an
    owner opened and gone from `sheet`, each block C `closed` whose uses
    edge onto B quotes the words of a part of A it named, and which holds
    no edge onto A; with why, and B, whose edit opens C."""
    uses = "uses"
    named = {
        (e.source, e.target, p) for e in sheet.edges if e.kind == uses for p in e.parts
    }
    found: dict[str, tuple[str, str]] = {}
    for dropped in shown.edges:
        if dropped.kind != uses or dropped.source not in opened:
            continue
        b, a = dropped.source, dropped.target
        for p in dropped.parts:
            if (b, a, p) in named:
                continue  # the part is still named, by this edge or another
            words = es.norm(shown.legend.get((a, p), ""))
            for c in sheet.edges:
                if (
                    words
                    and c.kind == uses
                    and c.target == b
                    and closed(c.source)
                    and words in es.norm(c.evidence)
                    and not any(
                        e.source == c.source and e.target == a for e in sheet.edges
                    )
                ):
                    found.setdefault(
                        c.source,
                        (
                            f"it cites {b}, which dropped its edge onto {a} [{p}], "
                            f"for {a} [{p}]'s words",
                            b,
                        ),
                    )
    return found


def new_words_owners(
    shown: es.Sheet, sheet: es.Sheet, closed: Callable[[str], bool]
) -> dict[str, tuple[str, str]]:
    """The reverse prefilter: for each part of `sheet`'s legend whose words
    no legend line of `shown` held, each block `closed` but its own whose
    text holds them, with no edge onto it; with why, and the part's
    block."""
    held = {es.norm(w) for w in shown.legend.values()}
    found: dict[str, tuple[str, str]] = {}
    for (target, label), words in sheet.legend.items():
        if es.norm(words) in held:
            continue
        for handle in sheet.bodies:
            if (
                handle != target
                and handle not in sheet.groups
                and closed(handle)
                and es.quoted(sheet, handle, words)
                and not any(
                    e.source == handle
                    and e.target == target
                    and (label in e.parts or not e.parts)
                    for e in sheet.edges
                )
            ):
                found.setdefault(
                    handle,
                    (
                        f'it writes "{es.norm(words)}", {target} [{label}]\'s '
                        "words, new in this version, and has no edge onto it",
                        target,
                    ),
                )
    return found


def update_reach(
    spec: dict[str, Any], sheet: es.Sheet
) -> tuple[set[str], list[Impact]]:
    """What an update's answer leaves open: the owners reopened,
    and those `closure.impact_reach` reaches over the shown graph from the
    spec's roots, by every Δ line of the sheet and the rule's Δs; with the
    impacts, so the gate can ask a Δ of each block whose meaning the rule
    moved. A follow-up session's spec names `earlier`, the owners a session
    before it left open: the walk goes on through them by their Δs as
    shown, and opens none of them, nor gives an impact on one."""
    found = spec["update"]
    graph = graph_from_payload(found["graph"])
    always = set(spec["reopened"])
    # A Δ line on a block the walk never reaches decides nothing, so every
    # Δ line is read; one on a fixed block is refused apart.
    deltas: dict[str, dict[str | None, str]] = {}
    for d in sheet.deltas:
        if d.cls in es.IMPACT_CLASSES:
            deltas.setdefault(d.source, {})[d.label or None] = d.cls
    pseudo: list[str] = []
    for handle, entry, cls in found["rule_deltas"]:
        deltas.setdefault(handle, {})[entry or None] = cls
        if handle not in pseudo:
            pseudo.append(handle)
    earlier = set(found.get("earlier", ()))
    impacts = [
        i
        for i in impact_reach(graph, deltas, [*found["roots"], *pseudo])
        if i.block not in earlier
    ]
    return always | {i.block for i in impacts}, impacts


#: A quotation in a Δ line's note: `"…"` or `“…”`.
QUOTE = re.compile(r'"([^"]+)"|“([^”]+)”')


#: The fewest words a closing Δ's quoted evidence holds.
QUOTE_WORDS_MIN = 3


def closing_problems(
    sheet: es.Sheet, deltas: Sequence[es.DeltaLine], changed: Collection[str]
) -> list[str]:
    """Each Δ that closes a change without its evidence. A change is stopped
    only beyond doubt. On a block whose text did not change (not in
    `changed`), `reworded` cannot be true, and `unchanged` quotes in its
    note words of the block, or of a block that qualifies it and was not
    edited, that show the edit does not move it (three words
    or more). On a changed block, `unchanged` for the block whole quotes
    the same way, the block's own words among them; a part's `unchanged`
    has its own words for evidence."""
    found = []
    for d in deltas:
        moved = d.source not in changed
        if d.cls == "reworded" and moved:
            found.append(
                f"line {d.line}: {d.source} Δ reworded: its text did not change, so "
                "it was not reworded; write `unchanged` with its evidence quoted, or "
                "the class that carries"
            )
            continue
        if d.cls != "unchanged" or (not moved and d.label):
            continue
        quotes = [
            q
            for a, b in QUOTE.findall(d.note or "")
            if len((q := a or b).split()) >= QUOTE_WORDS_MIN
        ]
        holders = [d.source] + [
            e.source
            for e in sheet.edges
            if e.kind == "qualifies"
            and e.target == d.source
            and not (moved and e.source in changed)
        ]
        if not any(es.quoted(sheet, h, q) for q in quotes for h in holders):
            found.append(
                f"line {d.line}: {d.source} Δ unchanged closes the change, so its "
                f"note quotes, in double quotes, three words or more of "
                f"{d.source} or of a block that qualifies it"
                + (" and was not edited" if moved else "")
                + ' that show the edit does not move it (`Δ unchanged — "<words>": '
                "why`); in doubt, write the class that carries"
            )
    return found


def _update_problems(spec: dict[str, Any], edited: str) -> list[str]:
    """An update's answer against what it was shown: the sheet's
    check with every owner fixed but those `update_reach` leaves open (an
    owner open on condition and closed after all keeps its lines, its done
    mark aside); Δ lines only on open blocks, or as shown on a fixed one (a
    follow-up session's earlier Δs); each Δ line owed (one per part of a
    changed block, else one for it; one on each block whose meaning the
    rule moved); the ⟦was⟧ and condition lines as shown. A part whose words
    the edit removed (`gone`) may leave the legend; its Δ then says
    `removed`."""
    sheet, shown = es.parse(edited), es.parse(spec["shown"])
    found_update = spec["update"]
    opened, impacts = update_reach(spec, sheet)
    closed = set(found_update["conditional"]) - opened
    gone = {(h, label) for h, label in found_update.get("gone", ())}
    found = problems(
        spec["reference"],
        edited,
        spec["block_ids"],
        spec["files"],
        spec["shown"],
        opened,
        spec.get("within"),
        undone=closed,
        gone=gone,
        claims_fixed=spec.get(CLAIMS_FIXED, ()),
    )
    found += group_line_problems(edited, opened, spec.get("pins", ()))
    for d in sheet.deltas:
        part = (d.source, d.label)
        dropped = part in gone and part not in sheet.legend
        if dropped and d.cls != "removed":
            found.append(
                f"line {d.line}: {d.source} Δ [{d.label}] {d.cls}: its legend line "
                f"is gone, so its words are: write `Δ [{d.label}] removed`, or "
                "quote where the part stands now"
            )
    for e in sheet.edges:
        named = [p for p in e.parts if (e.target, p) in gone]
        dropped = [p for p in named if (e.target, p) not in sheet.legend]
        if dropped and e.source in opened:
            found.append(
                f"line {e.line}: {e.source} → {e.kind} {e.target} names "
                f"[{dropped[0]}], whose words the edit removed: name the block "
                "whole, or a part its text holds now"
            )
    as_shown = {(d.source, d.label, d.cls, d.note) for d in shown.deltas}
    for d in sheet.deltas:
        if (d.source, d.label, d.cls, d.note) in as_shown:
            continue
        if d.source not in opened and d.source not in sheet.groups:
            found.append(
                f"line {d.line}: {d.source} Δ: a Δ line stands only on a block open "
                f"in this pass, and {d.source} is fixed"
            )
    found += closing_problems(
        sheet,
        [d for d in sheet.deltas if (d.source, d.label, d.cls, d.note) not in as_shown],
        found_update["owed"],
    )
    given = {(d.source, d.label or None) for d in sheet.deltas}
    for handle, parts in found_update["owed"].items():
        for part in parts:
            if (handle, part or None) not in given:
                named = f" [{part}]" if part else ""
                found.append(
                    f"{OWED}{handle}{named}): its text or its reading changed; write "
                    f"`Δ{named} <class> — <note>` under {handle}"
                )
    with_delta = {d.source for d in sheet.deltas}
    moved: dict[str, Impact] = {}
    for impact in impacts:
        if impact.meaning_moved:
            moved.setdefault(impact.block, impact)
    for handle, impact in moved.items():
        if handle not in with_delta and handle not in found_update["owed"]:
            named = f" [{', '.join(impact.parts)}]" if impact.parts else ""
            found.append(
                f"{OWED}{handle}): the rule moved its meaning, by its "
                f"{impact.via.kind} of {impact.handle}{named}; write its Δ line"
            )
    if sheet.was != shown.was:
        found.append(
            "the ⟦was⟧ lines are not as shown: restore them; a changed block's "
            "old text is fixed"
        )
    if [(c.owner, c.text) for c in sheet.conditions] != [
        (c.owner, c.text) for c in shown.conditions
    ]:
        found.append(
            "the condition lines (⟦asked if …⟧, ⟦meaning moved by rule: …⟧) are "
            "not as shown: restore them"
        )
    return found


# --- an update in one session ------------------------------------------

#: A cut line the queue applied while the session ran, kept as a header line
#: of the sheet: the correction as the store writes it, so the sheet alone
#: says which cut it stands on.
APPLIED_MARK = "Applied: "


def plain_update_question(
    document: Document,
    cut: Cut,
    brief: Template,
    prompt: str,
    files: Sequence[str],
    sheet: str,
    *,
    identity: str,
    owed: Sequence[str],
    roles_open: Sequence[str],
    gone: Collection[tuple[str, str]] = (),
    pins: Sequence[tuple[str, str, str]] = (),
    claims_fixed: Collection[str] = (),
    paper: str | None = None,
    held: Mapping[str, Sequence[str]] | None = None,
    kept: Collection[str] = (),
    graph: EdgeGraph | None = None,
    roots: Sequence[str] = (),
    rule_deltas: Sequence[tuple[str, str | None, str]] = (),
    moved_lines_max: int = 0,
) -> Question:
    """The question an update's one session answers as a queue of the
    blocks the edit touched (`update_queue`) under `brief`, its step's
    brief: its key, and its check's spec, which the queue's tasks are
    drawn from and checked against (the workspace's sheet is the carried
    `sheet`, no host's: a queue serves it a block at a time). Its answer
    is the sheet once every task is settled. `owed`, the changed blocks,
    and `roles_open`, the re-cut blocks whose roles the session writes,
    are by id. The blocks' text and lines are fixed but the re-cut
    blocks'. Its check (`_plain_problems`) asks one Δ line or more of
    each owed block, or of a block it split, and none elsewhere; `gone`
    names the owed parts whose words the edit removed (their Δ is
    `removed` where the legend drops them). Where `paper` names the paper's
    directory, the session's cut lines are applied by its queue, splits
    first, and the blocks they make are served after
    (`plain_cut_applied`); `held`, a
    person's pinned `starts`, `blocks` and `groups`, refuses a cut line
    that would change one. `pins`, a person's lines carried onto the
    sheet, hold as in a re-ask; `kept`, the problems OLD's sheet held
    (`kept_problems`), are not refused. With `graph`, the carried graph
    by handle, the gate walks the session's Δ lines from `roots`, the
    changed blocks, and the rule's Δs (`rule_deltas`), and owes a Δ line
    of each block they move by rule, up to `moved_lines_max` such lines
    (`plain_moved`). The key adds the sheet shown, the re-cut blocks and
    the identity answer's key; the brief's version binds the writer's."""

    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    return _question(
        document,
        cut,
        brief,
        files,
        None,
        shown=sheet,
        prompt=prompt,
        key_extra={
            "shown": digest(sheet),
            "roles_open": sorted(roles_open),
            "identity": identity,
            **_pinned(pins),
        },
        spec_extra={
            **_pinned(pins),
            **kept_spec(kept),
            "shown": sheet,
            PLAIN: {
                "owed": list(owed),
                "roles_open": sorted(roles_open),
                "gone": [[h, label] for h, label in sorted(gone)],
                "handles": handles(document, cut),
                "paper": paper,
                # with its given handles, so the gate reads the session's
                # sheet, and carries it past a cut line, by the names it shows
                "cut": cut_payload(document, cut) | given_handles_payload(cut)
                if paper is not None
                else None,
                "held": {k: sorted(v) for k, v in (held or {}).items()},
                **(
                    {
                        "graph": graph_payload(graph),
                        "roots": list(roots),
                        "rule_deltas": [[h, e or "", c] for h, e, c in rule_deltas],
                        "moved_lines_max": moved_lines_max,
                    }
                    if graph is not None
                    else {}
                ),
            },
        },
        claims_fixed=claims_fixed,
    )


@dataclass(frozen=True)
class PlainView:
    """The cut an update's one session's sheet stands on: the
    question's, with the cut corrections its queue applied (`applied`, the
    sheet's `Applied:` lines; `cut` None where there are none, so the check
    reads no paper), and what the sheet is checked against on it: the
    sheet rendered, the block ids, the nested blocks' outer ones, every
    handle's id, the re-cut blocks by handle (a block a correction made
    among them), and each owed block's handles (itself, or the blocks a
    correction made of it)."""

    cut: Cut | None
    applied: tuple[CutCorrection, ...]
    reference: str
    block_ids: list[str]
    within: dict[str, str]
    handles: dict[str, str]
    roles_open: list[str]
    owed: dict[str, list[str]]


def applied_corrections(sheet_text: str) -> tuple[CutCorrection, ...]:
    """The cut corrections a sheet's `Applied:` header lines hold, in order.
    Raises `RecordError` or `ValueError` for one that does not read."""
    found = []
    for line in sheet_text.split("\n"):
        if es.BLOCK.match(line) or es.GROUP.match(line):
            break
        if line.startswith(APPLIED_MARK):
            found.append(
                correction_from_data(
                    json.loads(line.removeprefix(APPLIED_MARK)), EDGES_FILE
                )
            )
    return tuple(found)


def _question_cut(document: Document, plain: Mapping[str, Any]) -> Cut:
    payload = plain["cut"]
    return cut_from_payload(
        document,
        payload,
        CutRules(DisplayRule(payload["rules"]["displays"])),
        roles=True,
    )


def plain_view(
    spec: Mapping[str, Any], sheet_text: str, document: Document | None = None
) -> PlainView:
    """The cut `sheet_text` stands on and what it is checked against there
    (`PlainView`). With `Applied:` lines, the paper is `document`, else
    read from the spec's directory. Raises `ValueError` where an `Applied:`
    line does not read or apply."""
    plain = spec[PLAIN]
    applied = applied_corrections(sheet_text)
    asked: dict[str, str] = dict(plain["handles"])
    name = {ident: handle for handle, ident in asked.items()}
    if not applied:
        return PlainView(
            None,
            (),
            spec["reference"],
            list(spec["block_ids"]),
            dict(spec.get("within") or {}),
            asked,
            [name[b] for b in plain["roles_open"] if b in name],
            {b: [name[b]] for b in plain["owed"] if b in name},
        )
    if plain.get("cut") is None:
        raise ValueError("this session's gate applies no cut line: no paper given")
    if document is None:
        document = read_paper(Path(plain["paper"]))
    before = _question_cut(document, plain)
    corrected = corrected_cut(document, before, applied)
    if corrected.problems:
        raise ValueError(
            "an Applied line no longer applies: "
            + "; ".join(p.message for p in corrected.problems)
        )
    cut = corrected.cut
    found = structure(document, cut, spec["files"])
    now = handles(document, cut)
    name = {ident: handle for handle, ident in now.items()}
    made = made_from(before, cut)
    open_ids = [b for b in plain["roles_open"] if b in name] + [
        b for b in made if b not in plain["roles_open"]
    ]
    owed = {
        b: [name[x] for x in (b, *(m for m, o in made.items() if o == b)) if x in name]
        for b in plain["owed"]
    }
    return PlainView(
        cut,
        applied,
        found["reference"],
        found["block_ids"],
        found["within"],
        now,
        [name[b] for b in dict.fromkeys(open_ids)],
        {b: hs for b, hs in owed.items() if hs},
    )


def made_from(before: Cut, after: Cut) -> dict[str, str]:
    """Each block of `after` a correction made or changed, by id, with the
    block of `before` its start lay in."""
    held = {(b.id, b.hash) for b in before.blocks}
    found = {}
    for block in after.blocks:
        if (block.id, block.hash) in held:
            continue
        home = next(
            (
                b.id
                for b in before.blocks
                if b.extent.file == block.extent.file
                and b.extent.start <= block.extent.start < b.extent.end
            ),
            None,
        )
        if home is not None:
            found[block.id] = home
    return found


#: The lines that end a block's text in a written sheet.
AFTER_TEXT = (
    "→ ",
    "⊢ ",
    "⟦done ",
    f"{es.DELTA_MARK} ",
    es.CUT_MARK,
    f"{es.FLAG_MARK} ",
)


def _chunks(text: str) -> dict[str, tuple[int, int]]:
    """Each block's line and text, as a range of line numbers, by handle."""
    lines = text.split("\n")
    found: dict[str, tuple[int, int]] = {}
    for i, line in enumerate(lines):
        m = es.BLOCK.match(line)
        if not m:
            continue
        end = i + 1
        while end < len(lines) and not (
            es.BLOCK.match(lines[end])
            or es.GROUP.match(lines[end])
            or es.END.match(lines[end])
            or lines[end].startswith(AFTER_TEXT)
        ):
            end += 1
        while end > i + 1 and not lines[end - 1].strip():
            end -= 1
        found[m.group(1)] = (i, end)
    return found


def _unmarked(lines: Sequence[str]) -> str:
    return es.norm(es.SEGMENT.sub("", "\n".join(lines)))


def with_open_roles(reference: str, roles_open: Collection[str], edited: str) -> str:
    """`reference` with each re-cut block's line and marked text (by handle,
    `roles_open`) as `edited` writes them, where its id and its words are
    the reference's: the sheet's check then holds every other block line
    and every text fixed."""
    lines_of = reference.split("\n")
    lines = edited.split("\n")
    shown, written = _chunks(reference), _chunks(edited)
    for handle in sorted(
        (h for h in roles_open if h in shown and h in written),
        key=lambda h: -shown[h][0],
    ):
        (s0, s1), (w0, w1) = shown[handle], written[handle]
        before, after = lines_of[s0:s1], lines[w0:w1]
        was, now = es.BLOCK.match(before[0]), es.BLOCK.match(after[0])
        if was is None or now is None or was.group(3) != now.group(3):
            continue
        if _unmarked(before[1:]) != _unmarked(after[1:]):
            continue
        lines_of[s0:s1] = after
    return "\n".join(lines_of)


def _open_role_problems(sheet: es.Sheet, roles_open: Collection[str]) -> list[str]:
    """What is wrong with the roles written on the re-cut blocks: each a
    role or `none`; one on the block line where no part is marked, else the
    parts' roles in order, as the sheet shows them; a marker that starts no
    later part or repeats the role before it; and a standing on a block
    whose written roles hold no claim (the edges' claim correction is for a block whose
    roles are fixed)."""
    found: list[str] = []
    known = {*es.ROLES, es.NONE}
    standing = {s.source for s in sheet.supports}
    for h in roles_open:
        if h not in sheet.texts:
            continue
        names = sheet.roles.get(h, ())
        pieces = es.segments(sheet, h)
        marked = [role for role, _ in pieces[1:]]
        wrong = [r for r in (*names, *marked) if r not in known]
        if wrong:
            found.append(
                f"{h}: {wrong[0]!r} is not a role: one of {', '.join(es.ROLES)}, "
                f"or {es.NONE}"
            )
            continue
        if len(pieces) > 1 and not pieces[0][1]:
            found.append(
                f"{h}: a `⟦+ {pieces[1][0]}⟧` line starts it; give its first part's "
                "role on its block line"
            )
            continue
        if any(a == b for (a, _), (b, _) in zip(pieces, pieces[1:], strict=False)):
            found.append(
                f"{h}: a `⟦+ <role>⟧` marker continues the role before it; mark "
                "only where the role changes"
            )
            continue
        expected = "+".join(dict.fromkeys(role for role, _ in pieces))
        if "+".join(names) != expected:
            found.append(
                f"{h}: its block line names {'+'.join(names)}; where its parts are "
                f"marked it names their roles in order, `{expected}`, and else one "
                "role, a change of role marked `⟦+ <role>⟧` where it starts"
            )
            continue
        if h in standing and "claim" not in names:
            found.append(
                f"{h} ⊢: a standing on a re-cut block whose roles you write: give it "
                "the claim role, on its block line or a `⟦+ claim⟧` part"
            )
    return found


def _plain_problems(
    spec: dict[str, Any], edited: str, document: Document | None = None
) -> list[str]:
    """An update's one session against what it was shown, on the
    cut its sheet stands on (`plain_view`): the sheet's check on that cut's
    sheet with the re-cut blocks' roles as written (`with_open_roles`),
    every owner's lines the session's; the roles written
    (`_open_role_problems`); one Δ line or more on each owed block or a
    block a correction made of it, and none elsewhere; a part whose words
    the edit removed (`gone`) may leave the legend, its Δ then `removed`,
    and no edge may name it."""
    plain = spec[PLAIN]
    gone = {(h, label) for h, label in plain["gone"]}
    try:
        view = plain_view(spec, edited, document)
    except (ValueError, RecordError, OSError) as error:
        return [f"the `{APPLIED_MARK.strip()}` lines: {error}; restore them as shown"]
    sheet = es.parse(edited)
    found = problems(
        with_open_roles(view.reference, view.roles_open, edited),
        edited,
        view.block_ids,
        spec["files"],
        within=view.within,
        gone=gone,
        claims_fixed=spec.get(CLAIMS_FIXED, ()),
    )
    found += _open_role_problems(sheet, view.roles_open)
    found += group_line_problems(edited, pins=spec.get("pins", ()))
    changed = {h for hs in view.owed.values() for h in hs}
    moved = plain_moved(spec, view, sheet)
    for d in sheet.deltas:
        if d.source not in changed and d.source not in moved.reached:
            found.append(
                f"line {d.line}: {d.source} Δ: a Δ line stands only on a block whose "
                f"text the edit changed, or whose meaning your Δ lines move by "
                f"rule, and {d.source} is neither"
            )
        elif (
            (d.source, d.label) in gone
            and (d.source, d.label) not in sheet.legend
            and d.cls != "removed"
        ):
            found.append(
                f"line {d.line}: {d.source} Δ [{d.label}] {d.cls}: its legend line "
                f"is gone, so its words are: write `Δ [{d.label}] removed`, or "
                "quote where the part stands now"
            )
    for e in sheet.edges:
        dropped = [
            p
            for p in e.parts
            if (e.target, p) in gone and (e.target, p) not in sheet.legend
        ]
        if dropped:
            found.append(
                f"line {e.line}: {e.source} → {e.kind} {e.target} names "
                f"[{dropped[0]}], whose words the edit removed: name the block "
                "whole, or a part its text holds now"
            )
    # the block whole's line speaks for the edges onto it whole, its group
    # and its qualifies edges: a part's lines alone would close them unread
    given = {d.source for d in sheet.deltas if not d.label}
    found += [
        f"{OWED}{hs[0]}): its text or its reading changed; write "
        "`Δ <class> — <note>` for the block whole under "
        + " or ".join(hs)
        + ", and a part's line beside it "
        "where its parts changed differently"
        for hs in view.owed.values()
        if not given & set(hs)
    ]
    found += closing_problems(sheet, sheet.deltas, changed)
    found += [
        f"{OWED}{handle}): your Δ lines move its meaning by rule, by its "
        f"{impact.via.kind} of {impact.handle}"
        + (f" [{', '.join(impact.parts)}]" if impact.parts else "")
        + "; read it with the edit and write its Δ line for the block whole: "
        "`unchanged` closes the change there, any other class carries it on"
        for handle, impact in moved.owed.items()
    ]
    return found


@dataclass(frozen=True)
class PlainMoved:
    """What an update's one session's Δ lines move by rule over the graph
    it was shown: every block the walk moves (`reached`, by the sheet's
    handle, with the first edge that moved it), and those of them owing a
    Δ line now (`owed`: the frontier; none where it would pass the cap, so
    it carries wholly)."""

    reached: dict[str, Impact]
    owed: dict[str, Impact]


def asked_names(spec: Mapping[str, Any], view: PlainView) -> dict[str, str]:
    """Each handle of a one-session sheet as the sheet it was asked on
    names its block: itself, or, for a block the gate's cut lines made of
    an owed block, that block."""
    asked = {ident: handle for handle, ident in spec[PLAIN]["handles"].items()}
    piece_of = {h: asked[b] for b, hs in view.owed.items() for h in hs if b in asked}
    return {
        h: piece_of.get(h) or asked.get(ident, h) for h, ident in view.handles.items()
    }


def plain_moved(
    spec: Mapping[str, Any], view: PlainView, sheet: es.Sheet
) -> PlainMoved:
    """The rule walked from the changed blocks over the carried graph by
    the session's Δ lines (each on the block it was asked of, a block its
    cut lines made speaking for the one it came from) and the rule's Δs: a
    block it moves goes on by its own Δ, so each one it moves through a
    block with a Δ line (a changed block, or one read so before), and that
    has none itself, is owed one: the nearest unread ring, read before the
    walk goes past it. `moved_lines_max` bounds the
    Δ lines asked on moved blocks over the whole session: a frontier that
    would pass it is not asked and carries wholly. A spec without the
    graph (asked before the gate walked it) moves nothing."""
    plain = spec[PLAIN]
    if "graph" not in plain:
        return PlainMoved({}, {})
    graph = graph_from_payload(plain["graph"])
    asked = asked_names(spec, view)
    sheet_name: dict[str, str] = {}
    for h, a in asked.items():
        if h == a or a not in sheet_name:
            sheet_name[a] = h
    deltas: dict[str, dict[str | None, str]] = {}
    for d in sheet.deltas:
        if d.cls in es.IMPACT_CLASSES:
            held = deltas.setdefault(asked.get(d.source, d.source), {})
            label = d.label or None
            if held.get(label) in (None, *CLOSING):
                held[label] = d.cls
    roots = list(plain["roots"])
    for handle, entry, cls in plain["rule_deltas"]:
        deltas.setdefault(handle, {})[entry or None] = cls
        if handle not in roots:
            roots.append(handle)
    reached: dict[str, Impact] = {}
    frontier: dict[str, Impact] = {}
    for impact in impact_reach(graph, deltas, roots):
        if not impact.meaning_moved or impact.block in roots:
            continue
        reached.setdefault(impact.block, impact)
        # the nearest unread ring only: a block moved through one that has
        # no Δ line yet waits for that line, which may close the change; a
        # moved block owes its line for the block whole, which speaks for
        # every edge onto it
        if None not in deltas.get(impact.block, {}) and impact.handle in deltas:
            frontier.setdefault(impact.block, impact)
    given = sum(1 for b in reached if None in deltas.get(b, {}))
    named = {sheet_name.get(b, b): i for b, i in reached.items()}
    if given + len(frontier) > plain["moved_lines_max"]:
        return PlainMoved(named, {})
    return PlainMoved(named, {sheet_name.get(b, b): i for b, i in frontier.items()})


@dataclass(frozen=True)
class PlainApplied:
    """What the gate did with a sheet's cut lines (`plain_cut_applied`): the
    sheet rewritten on the cut they leave, with what it tells the session;
    or why they could not apply."""

    sheet: str | None
    message: str


def plain_cut_applied(
    spec: dict[str, Any], edited: str, document: Document | None = None
) -> PlainApplied | None:
    """A sheet's cut lines applied, as the update's queue applies an
    answer's at once (None where it wrote none, or no paper was given): its
    roles written on the re-cut blocks applied first, then its cut lines,
    then each block a split made given its share of the parts marked on the
    block it came from (`piece_roles`); the sheet carried onto that cut
    (`carry`), its Δ and flag lines kept on the block they stood under, or
    its first piece, the cut lines dropped and kept as `Applied:` header
    lines; every block done but those they made. A cut line the assembler
    refuses, or one that would change what a person pinned (`held`), is
    sent back instead, and nothing applies."""
    plain = spec[PLAIN]
    sheet = es.parse(edited)
    if not sheet.cuts or plain.get("cut") is None:
        return None
    if document is None:
        document = read_paper(Path(plain["paper"]))
    view = plain_view(spec, edited, document)
    cut = view.cut or _question_cut(document, plain)
    roles, _ = roles_written(
        edited, view.handles, document, cut, view.reference, view.roles_open
    )
    with_roles = corrected_cut(document, cut, roles).cut
    proposed = proposals(edited, view.handles)
    trial = corrected_cut(document, with_roles, proposed)
    refused = [p.message for p in trial.problems]
    held = plain.get("held") or {}
    changed = {
        b.id
        for b in with_roles.blocks
        if (b.id, b.hash) not in {(x.id, x.hash) for x in trial.cut.blocks}
    }
    starts = {b.id for b in with_roles.blocks} ^ {b.id for b in trial.cut.blocks}
    pinned = sorted(
        (changed & set(held.get("blocks", ()))) | (starts & set(held.get("starts", ())))
    ) + [
        c.group
        for c in proposed
        if c.group is not None and c.group in held.get("groups", ())
    ]
    if pinned:
        refused.append(f"it would change what a person pinned: {', '.join(pinned)}")
    if refused:
        lines = "; ".join(refused)
        return PlainApplied(
            None,
            f"your cut lines cannot be applied: {lines}. Correct them, or remove "
            "them and write the block as it is cut.",
        )
    pieces = piece_roles(document, with_roles, trial.cut)
    after = corrected_cut(document, with_roles, [*proposed, *pieces]).cut
    carried = carry(document, edited, cut, after, spec["files"])
    name = {ident: handle for handle, ident in handles(document, after).items()}
    kept: dict[str, list[str]] = {}
    for owner, lines in written_lines(edited, flags=True).items():
        ident = view.handles.get(owner)
        if ident in name:
            kept[name[ident]] = [
                line
                for line in lines
                if line.startswith(f"{es.DELTA_MARK} ")
                and line
                not in written_lines(carried.sheet, flags=True).get(name[ident], [])
            ]
    text = with_lines(carried.sheet, kept)
    applied = [*view.applied, *proposed]
    header = [
        APPLIED_MARK + json.dumps(correction_data(c), ensure_ascii=False)
        for c in applied
    ]
    text = _with_header(text, header)
    made = [name[b] for b in made_from(cut, after) if b in name]
    return PlainApplied(
        text,
        "your cut lines are applied: edges.md now stands on the cut they make, "
        f"and shows the blocks they made or changed ({', '.join(made)}) without "
        "a done mark. Write each of them its lines, as a block of its own, and "
        "mark it done. The cut lines are kept in the header as `Applied:` lines: "
        "leave them as they are.",
    )


def with_lines(text: str, extra: Mapping[str, Sequence[str]]) -> str:
    """`text` with `extra`'s lines added at the end of each owner's own."""
    out: list[str] = []
    owner: str | None = None

    def flush() -> None:
        if owner in extra and extra[owner]:
            at = len(out)
            while at and out[at - 1] == "":
                at -= 1
            out[at:at] = list(extra[owner])

    for line in text.split("\n"):
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            flush()
            owner = None if es.GROUP.match(line) else m.group(1)
        out.append(line)
    flush()
    return "\n".join(out)


def _with_header(text: str, header: Sequence[str]) -> str:
    """`text` with its `Applied:` lines replaced by `header`, above the
    `Files:` line."""
    lines = [line for line in text.split("\n") if not line.startswith(APPLIED_MARK)]
    at = next((n for n, line in enumerate(lines) if es.FILES.match(line)), 0)
    return "\n".join([*lines[:at], *header, *lines[at:]])


def piece_roles(document: Document, before: Cut, after: Cut) -> list[CutCorrection]:
    """A `ROLES` correction for each block of `after` cut from a block of
    `before` whose parts were placed, giving it the parts that fall in it:
    the part its start lies in, then each part that starts inside it (a
    split leaves each piece the whole block's roles, unsegmented). A block
    of one role, or none, needs none: each piece keeps it whole."""
    found: list[CutCorrection] = []
    for piece_id, home in made_from(before, after).items():
        source = before.block(home)
        if len(source.segments) < 2:
            continue
        piece = after.block(piece_id)
        starts = [source.owned[0].start] + [
            place.offset for place in part_lines(document, source)
        ]
        first = piece.owned[0].start
        inside = [
            seg
            for s, seg in zip(starts, source.segments, strict=True)
            if first < s < piece.extent.end
        ]
        covering = [
            seg for s, seg in zip(starts, source.segments, strict=True) if s <= first
        ]
        segments = [Segment("", covering[-1].role), *inside]
        roles = role_set(s.role for s in segments if s.role is not None)
        shaped: tuple[Segment, ...] = (
            ()
            if not roles
            else tuple(segments)
            if len(segments) > 1
            else (Segment("", roles[0]),)
        )
        found.append(
            CutCorrection(
                id="",
                op=CutOp.ROLES,
                cls=CorrectionClass.OTHER,
                why=PIECE_ROLES_WHY,
                block=piece_id,
                roles=roles,
                segments=shaped,
            )
        )
    return found


#: Why a block a session's split made has its roles: its share of the parts
#: the session marked on the block it came from.
PIECE_ROLES_WHY = "a split piece's share of the parts the update's session marked"


def roles_written(
    sheet_text: str,
    named: Mapping[str, str],
    document: Document,
    cut: Cut,
    reference: str,
    roles_open: Collection[str],
) -> tuple[tuple[CutCorrection, ...], tuple[Flag, ...]]:
    """The `ROLES` corrections an update's one session gives: one
    per re-cut block (by handle, `roles_open`, on `cut` as `named`) whose
    line or marked text it wrote otherwise than `reference` shows, its
    roles from the block line and each `⟦+ <role>⟧` part placed where its
    marker stands in the block's words, ids left for the resolver. A part
    that cannot be placed (no excerpt from its start stands once in the
    block) adds no correction but a `role` flag, so a valid answer is never
    refused for it."""
    sheet = es.parse(sheet_text)
    shown, written = _chunks(reference), _chunks(sheet_text)
    lines_of, lines = reference.split("\n"), sheet_text.split("\n")
    found: list[CutCorrection] = []
    raised: list[Flag] = []
    for handle in roles_open:
        if handle not in shown or handle not in written:
            continue
        (s0, s1), (w0, w1) = shown[handle], written[handle]
        if lines_of[s0:s1] == lines[w0:w1]:
            continue
        block = cut.block(named[handle])
        try:
            roles, segments = _written_roles(
                document, block, es.segments(sheet, handle)
            )
        except Exception as error:  # noqa: BLE001 - a paid answer is never lost
            kind = "" if isinstance(error, ValueError) else f"{type(error).__name__}: "
            raised.append(
                Flag(
                    id="",
                    answer=None,
                    blocks=(block.id,),
                    signal=Signal.ROLE,
                    evidence=(" ".join(sheet.bodies[handle].split()[:EXCERPT_WORDS]),),
                    note=f"{ROLES_NOT_PLACED}: {kind}{error}",
                )
            )
            continue
        found.append(
            CutCorrection(
                id="",
                op=CutOp.ROLES,
                cls=CorrectionClass.OTHER,
                why=ROLES_WRITTEN_WHY,
                block=block.id,
                roles=roles,
                segments=segments,
            )
        )
    return tuple(found), tuple(raised)


def _written_roles(
    document: Document, block: Block, pieces: Sequence[tuple[str, str]]
) -> tuple[tuple[Role, ...], tuple[Segment, ...]]:
    """A block's roles and segments from its written parts, `(role,
    normalised text)` in order (`edge_sheet.segments`): each later part
    starts at the block's owned word its text starts at, counted in the
    words the parts hold before it (the sheet shows the owned runs' words,
    a run break between), its start the fewest words from there that stand
    once (`_excerpt`). Raises `ClaimNotPlaced` where none do, `ValueError`
    where the roles make no block."""
    text = document.file(block.extent.file).text
    starts = [
        run.start + m.start()
        for run in block.owned
        for m in re.finditer(r"\S+", text[run.start : run.end])
    ]
    segments: list[Segment] = []
    before = 0
    for n, (role, words) in enumerate(pieces):
        made = None if role == es.NONE else Role(role)
        if n == 0:
            segments.append(Segment("", made))
        else:
            if before >= len(starts):
                raise ClaimNotPlaced(f"its `⟦+ {role}⟧` part starts past its text")
            segments.append(Segment(_excerpt(document, block, starts[before]), made))
        before += len([w for w in words.split() if w != es.RUN_BREAK])
    roles = role_set(s.role for s in segments if s.role is not None)
    if not roles:
        return (), ()
    shaped = tuple(segments) if len(segments) > 1 else (Segment("", roles[0]),)
    problem = roles_problem(roles, shaped)
    if problem is not None:
        raise ValueError(f"the roles written make no block's roles: {problem}")
    return roles, shaped


def _question(
    document: Document,
    cut: Cut,
    template: Template,
    files: Sequence[str],
    limit: int | None,
    *,
    shown: str | None = None,
    reopened: Collection[str] | None = None,
    instructions: str | None = None,
    prompt: str = KICKOFF,
    key_extra: dict[str, Any] | None = None,
    spec_extra: dict[str, Any] | None = None,
    claims_fixed: Collection[str] = (),
    lines: bool = False,
) -> Question:
    """The session's question; `shown` is the sheet it starts from (the
    rendered sheet when None), which the check compares with the rendered
    one, so the fixed lines and the text are fixed in every pass. With
    `reopened`, every other block's and group's own lines are fixed as
    `shown` has them; `spec_extra` adds to what the
    gate reads (an update's). `claims_fixed`, by block id, are
    the blocks whose roles a person pinned: the gate
    refuses a standing there that would add the claim role.
    They stand in the gate's spec only where there are any, and in no
    key: a person's roles answer moves the cut the key binds already.
    With `lines`, the session reads the sheet and
    writes its lines by owner in `LINES_FILE`, checked when it tries to
    finish only."""
    blocks = sheet_blocks(document, cut)
    groups = sheet_groups(document, cut)
    try:
        sheet = es.render(blocks, groups, files, HEADER)
    except es.RenderError as error:
        raise RenderError(f"the cut cannot be shown in {EDGES_FILE}: {error}") from None
    start = sheet if shown is None else shown
    if limit is not None and len(start) > limit:
        raise RenderError(
            f"{EDGES_FILE} would hold {len(start):,} characters, more than a "
            f"session reads ({limit:,}); the edges stage asks one session per "
            "paper"
        )
    by_block = es.block_handles(blocks)
    group_names = es.group_handles(groups, by_block)
    handles = {
        **{h: block for block, h in by_block.items()},
        **{h: g.id for g, h in zip(cut.groups, group_names, strict=True)},
    }
    key_inputs: dict[str, Any] = {
        "blocks": [
            {
                "id": block.id,
                "hash": block.hash,
                "form": block.form.value,
                "segments": [list(s) for s in shown_block.segments]
                or [["", r] for r in shown_block.roles],
            }
            for block, shown_block in zip(cut.blocks, blocks, strict=True)
        ],
        "groups": [
            {"kind": g.kind.value, "blocks": list(g.blocks)} for g in cut.groups
        ],
        "files": list(files),
        **(key_extra or {}),
    }
    spec = json.dumps(
        {
            "reference": sheet,
            "block_ids": [b.id for b in cut.blocks],
            "files": list(files),
            "within": {
                by_block[b.id]: by_block[b.within]
                for b in cut.blocks
                if b.within is not None and b.within in by_block
            },
            **(
                {"shown": start, "reopened": sorted(reopened)}
                if reopened is not None
                else {}
            ),
            **(spec_extra or {}),
            **(
                {
                    CLAIMS_FIXED: sorted(
                        by_block[b] for b in claims_fixed if b in by_block
                    )
                }
                if any(b in by_block for b in claims_fixed)
                else {}
            ),
            **({LINES: True} if lines else {}),
        },
        ensure_ascii=False,
    )
    return Question(
        key=question_key(Stage.EDGES, template, key_inputs, KIND),
        kind=KIND,
        stage=Stage.EDGES,
        template=template,
        prompt=prompt,
        handles=handles,
        input_cut=cut,
        workspace=Workspace(
            files={
                EDGES_FILE: start,
                **({LINES_FILE: ""} if lines else {}),
            },
            edit=LINES_FILE if lines else EDGES_FILE,
            instructions=template.render() if instructions is None else instructions,
            reask_from_edit=True,
            gate=Gate(module=__name__, spec=spec, on_write=not lines),
        ),
    )


# --- lines by owner -----------------------------------------------------------------


def by_owner(written: str) -> tuple[dict[str, list[str]], list[str]]:
    """`written`'s lines under each `@@ <handle>` heading, blank lines left
    out, and the lines before any heading."""
    found: dict[str, list[str]] = {}
    before: list[str] = []
    owner: str | None = None
    for line in written.split("\n"):
        if line.startswith("@@"):
            owner = line[2:].strip()
            found.setdefault(owner, [])
        elif not line.strip():
            continue
        elif owner is None:
            before.append(line)
        else:
            found[owner].append(line)
    return found, before


def laid(sheet: str, written: str) -> str:
    """The lines by owner `written` laid into `sheet`: the header's above
    its `---` line, and each block's and group's after its text, closed by
    its done mark; an owner with no heading is left open."""
    lines, _ = by_owner(written)
    out: list[str] = []
    owner: str | None = None

    def close() -> None:
        if owner is None or owner not in lines:
            return
        at = len(out)
        while at and out[at - 1] == "":
            at -= 1
        out[at:at] = [*lines[owner], f"⟦done {owner}⟧"]

    for line in sheet.split("\n"):
        if line == "---" and owner is None:
            out.extend(lines.get(LINES_HEADER, []))
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            close()
            owner = None if es.GROUP.match(line) else m.group(1)
        out.append(line)
    close()
    return "\n".join(out)


#: A problem's line number is the laid sheet's, which the session never
#: sees; the problem quotes the line itself.
_LINE_NUMBER = re.compile(r"^line \d+: ")


def _lines_problems(
    spec: dict[str, Any], written: str, document: Document | None
) -> list[str]:
    """What the gate finds wrong with lines by owner: a line before any
    heading, a heading that names no block or group, then the sheet's
    problems with the lines laid into it, an owner not written named by
    its heading."""
    lines, before = by_owner(written)
    sheet = laid(spec["reference"], written)
    owners = {
        m.group(1)
        for line in spec["reference"].split("\n")
        if (m := (es.BLOCK.match(line) or es.END.match(line)))
    }
    found = [
        *(f"a line before any `@@` heading: '{line}'" for line in before),
        *(
            f"`@@ {h}`: no block or group has this handle; its lines are not read"
            for h in lines
            if h != LINES_HEADER and h not in owners
        ),
    ]
    for problem in spec_problems({**spec, LINES: False}, sheet, document):
        problem = _LINE_NUMBER.sub("", problem)
        if problem.startswith(es.UNFINISHED[0]):
            handle = problem[len(es.UNFINISHED[0]) :]
            problem = f"{problem}: no `@@ {handle}` heading in {LINES_FILE}"
        found.append(problem)
    return found


# --- a later pass's changes ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Changes:
    """A later pass's `lines.md` read against the sheet it changes: the
    header's lines it removes and adds, its owners' lines as a fragment,
    each `-` line resolved to the one line it names, and the changes that
    do not apply."""

    removed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    fragment: Fragment = field(default_factory=lambda: Fragment())
    problems: tuple[str, ...] = ()


def _header_line(line: str) -> str:
    """A header line as the sheet holds it: a legend line with its two
    leading spaces, which a change may leave out."""
    return line if es.PERVASIVE.match(line) else f"  {line.lstrip()}"


def changes(sheet: str, written: str) -> Changes:
    """`written`'s `+` and `-` lines by owner read against `sheet`: under
    `@@ the header` a legend line or the `Pervasive:` line, named whole;
    under a block or a group any of its lines, a `-` line named whole or
    by its head and quote (`named_lines`). Refused: a line before any
    heading, a heading that names no owner, a line that is no change, a
    `-` line that names no one line its owner holds, a `+` line it holds
    and does not remove, a second `Pervasive:` line, and no `DONE_MARK` as
    the last line."""
    rows = written.rstrip().split("\n")
    finished = rows[-1].strip() == DONE_MARK
    by, before = by_owner("\n".join(rows[:-1] if finished else rows))
    owners = {
        m.group(1)
        for x in sheet.split("\n")
        if (m := es.BLOCK.match(x) or es.END.match(x))
    }
    head = sheet.split(f"\n{es.RULE}\n", 1)[0].split("\n")
    held = {
        o: [x for x in lines if not es.DONE.match(x)]
        for o, lines in written_lines(sheet, flags=True).items()
    }
    found = [f"a line before any `@@` heading: '{x}'" for x in before]
    removed: list[str] = []
    added: list[str] = []
    owned: list[tuple[str, str, str]] = []
    for owner, lines in by.items():
        if owner != LINES_HEADER and owner not in owners:
            found.append(
                f"`@@ {owner}`: no block or group has this handle; its lines are "
                "not read"
            )
            continue
        own = held.get(owner, [])
        for row in lines:
            op, _, line = row.strip().partition(" ")
            line = line.strip()
            if op not in (ADD, RETRACT) or not line:
                found.append(
                    f"`@@ {owner}`: a change begins with `+ ` or `- `: '{row}'"
                )
            elif owner == LINES_HEADER:
                line = _header_line(line)
                if not (es.LEGEND.match(line) or es.PERVASIVE.match(line)):
                    found.append(
                        f"`@@ {LINES_HEADER}`: a legend line or the `Pervasive:` "
                        f"line only: '{row}'"
                    )
                elif op == RETRACT and line not in head:
                    found.append(f"the header holds no line `{line.strip()}`")
                else:
                    (removed if op == RETRACT else added).append(line)
            elif es.DONE.match(line) or line == DONE_MARK:
                found.append(
                    f"`@@ {owner}`: write no done mark; `{DONE_MARK}` ends the file"
                )
            elif op == ADD and (owner, op, line) in owned:
                found.append(f"`@@ {owner}`: the line `{line}` is added twice")
            elif op == RETRACT:
                named = named_lines(own, line)
                if len(named) == 1:
                    owned.append((owner, op, named[0]))
                else:
                    found.append(_unnamed(owner, line, named, own))
            else:
                owned.append((owner, op, line))
    taken = {(o, x) for o, op, x in owned if op == RETRACT}
    found += [
        f"⟦{o}⟧ holds the line `{x}` already"
        for o, op, x in owned
        if op == ADD and x in held.get(o, []) and (o, x) not in taken
    ]
    found += [
        f"the header holds the line `{x.strip()}` already"
        for x in added
        if x in head and x not in removed
    ]
    if not finished:
        found.append(
            f"not finished: end {LINES_FILE} with `{DONE_MARK}` on its own line once "
            "every change is written"
        )
    pervasive = [x for x in head if es.PERVASIVE.match(x) and x not in removed]
    if len(pervasive) + sum(1 for x in added if es.PERVASIVE.match(x)) > 1:
        found.append(
            "one `Pervasive:` line: remove the sheet's (`- Pervasive: …`) to write "
            "another"
        )
    return Changes(
        tuple(removed), tuple(added), Fragment(lines=tuple(owned)), tuple(found)
    )


def applied_changes(sheet: str, written: str) -> str:
    """`sheet` with a later pass's changes applied: the header's lines
    removed, then added above its rule; each owner's `-` lines taken off and
    its `+` lines added after its last line, and the standings they argue
    (`applied_fragment`). Refused (`FragmentError`) with every change that
    does not apply (`changes`)."""
    found = changes(sheet, written)
    if found.problems:
        raise FragmentError("\n".join(found.problems))
    return _applied(sheet, found)


def _applied(sheet: str, found: Changes) -> str:
    head, rule, rest = sheet.partition(f"\n{es.RULE}\n")
    rows = [x for x in head.split("\n") if x not in found.removed]
    rows += found.added
    return applied_fragment("\n".join(rows) + rule + rest, found.fragment)


def as_changes(sheet: str, edited: str) -> str:
    """The changes that make `edited` of `sheet`, as a later pass writes
    them in `lines.md`: the header's legend and `Pervasive:` lines, then
    each owner's, each line no longer held removed whole and each new one
    added; done marks left out; `DONE_MARK` last."""

    def header(text: str) -> list[str]:
        return [
            x
            for x in text.split(f"\n{es.RULE}\n", 1)[0].split("\n")
            if es.LEGEND.match(x) or es.PERVASIVE.match(x)
        ]

    def diff(before: Sequence[str], after: Sequence[str]) -> list[str]:
        return [f"{RETRACT} {x.strip()}" for x in before if x not in after] + [
            f"{ADD} {x.strip()}" for x in after if x not in before
        ]

    out: list[str] = []
    if lines := diff(header(sheet), header(edited)):
        out += [f"@@ {LINES_HEADER}", *lines]
    old, new = written_lines(sheet, flags=True), written_lines(edited, flags=True)
    for owner in dict.fromkeys([*old, *new]):
        if lines := diff(
            [x for x in old.get(owner, []) if not es.DONE.match(x)],
            [x for x in new.get(owner, []) if not es.DONE.match(x)],
        ):
            out += [f"@@ {owner}", *lines]
    return "\n".join([*out, DONE_MARK])


def _changes_problems(
    spec: dict[str, Any], written: str, document: Document | None
) -> list[str]:
    """What the gate finds wrong with a later pass's changes: those that
    do not apply, else the sheet's problems with them applied."""
    found = changes(spec[CHANGES], written)
    if found.problems:
        return list(found.problems)
    try:
        sheet = _applied(spec[CHANGES], found)
    except FragmentError as error:
        return [str(error)]
    whole = {k: v for k, v in spec.items() if k not in (LINES, CHANGES)}
    return [_LINE_NUMBER.sub("", p) for p in spec_problems(whole, sheet, document)]


# --- reading the sheet --------------------------------------------------------------


def _fixed_lines(text: str) -> list[str]:
    return [
        line
        for line in text.split("\n")
        if es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)
    ]


def problems(
    reference: str,
    edited: str,
    block_ids: list[str],
    files: Sequence[str],
    shown_sheet: str | None = None,
    reopened: Collection[str] | None = None,
    within: dict[str, str] | None = None,
    undone: Collection[str] = (),
    gone: Collection[tuple[str, str]] = (),
    claims_fixed: Collection[str] = (),
) -> list[str]:
    """What is wrong with an edited sheet against the one shown: the
    sheet's check, then what the product adds to it; with `reopened`, any
    change to the lines of a block or group not reopened, but
    a done mark added to an owner in `undone`, shown without one (an
    update's owner open on condition and closed); `gone`, the
    parts an update's edit removed, which a Δ line may still name;
    `claims_fixed`, the blocks whose roles a person gave, on which a
    standing adds no claim role."""
    found = es.check(edited, block_ids, within, gone, claims_fixed)
    if reopened is not None and shown_sheet is not None:
        found += _fixed_problems(shown_sheet, edited, set(reopened), set(undone), gone)
    shown, sheet = es.parse(reference), es.parse(edited)
    if _fixed_lines(edited) != _fixed_lines(reference):
        found.append(
            "the block and group lines are not as shown: restore them; the cut, "
            "the roles and the groups are fixed"
        )
    found += [
        f"{h}: the block's text changed; restore it word for word"
        for h, body in shown.bodies.items()
        if h in sheet.bodies and es.norm(sheet.bodies[h]) != es.norm(body)
    ]
    # The sheet's check lets a sheet with no support line through, as a key's
    # first pass; a writer gives every claim's standing.
    claims = [h for h, roles in sheet.roles.items() if "claim" in roles]
    if claims and not sheet.supports:
        found += [f"no support line: {h}" for h in claims]
    for e in sheet.edges:
        where = f"line {e.line}: {e.source} → {e.kind} {e.target}".rstrip()
        if e.optional:
            found.append(f"{where}: `optional` is not a writer's flag; remove it")
        if e.target.startswith(es.FILE):
            problem = _file_problem(e.target.removeprefix(es.FILE), files, shown)
            if problem:
                found.append(f"{where}: {problem}")
    for (target, label), role in sheet.legend_roles.items():
        if role == es.STANDARD:
            found.append(
                f"part {target} [{label}]: `(standard)` is not a writer's role"
            )
    for target, label in sheet.legend:
        if target in sheet.groups:
            found.append(f"part {target} [{label}]: a part is a block's, not a group's")
    return found


def _fixed_problems(
    shown: str,
    edited: str,
    reopened: set[str],
    undone: Collection[str] = (),
    gone: Collection[tuple[str, str]] = (),
) -> list[str]:
    """What a re-ask changed outside the blocks it reopens:
    their lines, the legend's parts of their own, and their place in the
    `Pervasive:` line. A new part may name a block only for a reopened
    block's edge. An owner in `undone` is shown without its done mark and
    may take one. A part in `gone`, whose words an update's edit removed,
    is its reopened block's to quote anew or remove, whatever fixed edge
    names it: the edge follows its Δ."""
    found = []
    before, now = written_lines(shown), written_lines(edited)
    for h in undone:
        for held in (before, now):
            if h in held:
                held[h] = [line for line in held[h] if not es.DONE.match(line)]
    found += [
        f"{h}: its lines are fixed in this pass, which reopens only the "
        "blocks without a done mark; restore them as shown"
        for h in sorted(set(before) | set(now))
        if h not in reopened and before.get(h, []) != now.get(h, [])
    ]
    old, new = es.parse(shown), es.parse(edited)

    def part(sheet: es.Sheet, key: tuple[str, str]) -> tuple[str, str | None]:
        return sheet.legend[key], sheet.legend_roles.get(key)

    used = {
        (e.target, label)
        for e in new.edges
        if e.source in reopened
        for label in e.parts
    }
    # A part a fixed block's edge names is fixed with it, on whatever block.
    held = {
        (e.target, label)
        for e in old.edges
        if e.source not in reopened
        for label in e.parts
    }
    for key in sorted(set(old.legend) | set(new.legend)):
        target, label = key
        if target in reopened and (key not in held or key in gone):
            continue
        if key in old.legend and (
            key not in new.legend or part(old, key) != part(new, key)
        ):
            found.append(
                f"part {target} [{label}]: fixed in this pass, as {target} or an "
                "edge that names it is not reopened; restore its legend line as "
                "shown"
            )
        elif key not in old.legend and key not in used:
            found.append(
                f"part {target} [{label}]: a new part on {target}, which is not "
                "reopened, is for a reopened block's edge only"
            )
    moved = set(old.pervasive) ^ set(new.pervasive)
    found += [
        f"Pervasive: {h} is fixed in this pass, as it is not reopened"
        for h in sorted(moved - reopened)
    ]
    return found


def _file_problem(path: str, files: Sequence[str], shown: es.Sheet) -> str | None:
    """Why `path` is not a file of the paper, else None. A file of the paper
    is one on disk (the `Files:` line, or a `Code:` line's key),
    or a path the text writes whole,
    such as a script the paper says recomputes a number but does not ship.
    A file on disk
    named without its extension is refused with its name; a word is never
    taken for a path."""
    if path in files:
        return None
    stems = [f for f in files if Path(f).stem == path or Path(f).name == path]
    if stems:
        return f"write the file's name as the header names it: file:{stems[0]}"
    # A full stop after the path ends a sentence, not the path.
    whole = re.compile(rf"(?<![\w./-]){re.escape(path)}(?![\w/-]|\.[\w/-])")
    looks_like_a_path = "/" in path or "." in path
    if looks_like_a_path and any(whole.search(t) for t in shown.texts.values()):
        return None
    # the header lists the files; the message names none, so it says the
    # same wherever the paper's files change (docs/architecture.md § 9)
    return (
        "not a file of the paper: neither in the header's Files: or Code: lines "
        "nor a path the text writes"
    )


def records(
    sheet_text: str, handles: dict[str, str], deltas: bool = False
) -> EdgeRecords:
    """A sheet that passed `problems` as the edge stage's records, ids left
    for the resolver, each kind in the sheet's order; with `deltas`, an
    update's answer, its Δ lines too."""
    sheet = es.parse(sheet_text)

    def target(name: str) -> EdgeTarget:
        if name.startswith(es.FILE):
            return EdgeTarget(TargetKind.FILE, name.removeprefix(es.FILE))
        if name == es.SCOPE_PAPER:
            return EdgeTarget(TargetKind.SCOPE, "paper")
        if name in sheet.groups:
            return EdgeTarget(TargetKind.GROUP, handles[name])
        return EdgeTarget(TargetKind.BLOCK, handles[name])

    def role(name: str) -> Role | None:
        return None if name == es.NONE else Role(name)

    entries = tuple(
        Entry(
            id="",
            answer=None,
            block=handles[h],
            label=label,
            words=words,
            role=role(es.part_role(sheet, h, label)),
            # A hand key's notation only (`problems` refuses it from a
            # writer); kept so a key's sheet goes through unchanged.
            standard=sheet.legend_roles.get((h, label)) == es.STANDARD,
        )
        for (h, label), words in sheet.legend.items()
    )
    edges = tuple(
        Edge(
            id="",
            answer=None,
            source=handles[e.source],
            kind=EdgeKind(e.kind),
            target=target(e.target),
            evidence=(e.evidence,),
            parts=es.legend_labels(sheet, e.parts, e.target),
            mark=Mark(e.mark) if e.mark else None,
            where_used=e.where_used,
            note=e.note or None,
            partial=Partial(e.partial) if e.partial else None,
            generalizes=e.generalizes,
            claims=es.named_claims(sheet, e.target, e.parts),
            source_claims=es.named_claims(sheet, e.source, e.own),
            restates_none=e.own == (es.RESTATES_NONE,),
            assumed=e.assumed,
        )
        for e in sheet.edges
        if e.kind != es.SELF
    )
    selfs = tuple(
        SelfMark(
            id="",
            answer=None,
            block=handles[e.source],
            evidence=(e.evidence,),
            note=e.note or None,
            partial=Partial(e.partial) if e.partial else None,
            claims=es.named_claims(sheet, e.source, e.own),
        )
        for e in sheet.edges
        if e.kind == es.SELF
    )
    shown = es.shown_handles(sheet)
    standings = tuple(
        Standing(
            id="",
            answer=None,
            block=handles[s.source],
            claim=s.claim,
            status=Status(s.status),
            note=s.note or None,
            declared=Declared(s.declared) if s.declared else None,
            handle=shown.get(s.line),
        )
        for s in sheet.supports
    )
    frame = tuple(Frame(id="", answer=None, block=handles[h]) for h in sheet.pervasive)
    written_deltas = tuple(
        Delta(
            id="",
            answer=None,
            block=handles[d.source],
            entry=d.label or None,
            cls=ImpactClass(d.cls),
            note=d.note,
        )
        for d in sheet.deltas
        if deltas
    )
    return EdgeRecords(
        entries,
        edges,
        selfs,
        standings,
        frame,
        written_deltas,
        line_orders(sheet, handles),
    )


def line_orders(sheet: es.Sheet, handles: Mapping[str, str]) -> tuple[LineOrder, ...]:
    """Each owner's lines by kind in the sheet's order, where kinds
    interleave: an owner whose edges, self marks and standings stand in
    that order has none."""
    lines: dict[str, list[tuple[int, LineKind]]] = {}
    for e in sheet.edges:
        kind = LineKind.SELF if e.kind == es.SELF else LineKind.EDGE
        lines.setdefault(e.source, []).append((e.line, kind))
    for s in sheet.supports:
        lines.setdefault(s.source, []).append((s.line, LineKind.STANDING))
    found = []
    for owner, held in lines.items():
        kinds = tuple(k for _, k in sorted(held, key=lambda x: x[0]))
        if list(kinds) != sorted(kinds, key=_KIND_RANK.__getitem__):
            found.append(
                LineOrder(id="", answer=None, owner=handles[owner], kinds=kinds)
            )
    return tuple(found)


#: The order an owner's lines take where no `LineOrder` says otherwise.
_KIND_RANK = {LineKind.EDGE: 0, LineKind.SELF: 1, LineKind.STANDING: 2}


def proposals(sheet_text: str, handles: dict[str, str]) -> tuple[CutCorrection, ...]:
    """A sheet's cut lines as cut corrections, in the sheet's order, ids
    left for the resolver: a block's splits as one split at
    each of their words, a run of merges, each onto the next, as one merge,
    a move as a rebound."""
    found: list[CutCorrection] = []
    sheet = es.parse(sheet_text)
    splits: dict[str, list[es.CutLine]] = {}
    for c in sheet.cuts:
        if c.op == "split":
            splits.setdefault(c.source, []).append(c)
    merged_into: dict[str, int] = {}  # a block's merge, by its last block
    for c in sheet.cuts:
        if c.op == "split":
            lines = splits.pop(c.source, None)
            if lines:
                found.append(
                    CutCorrection(
                        id="",
                        op=CutOp.SPLIT,
                        cls=CorrectionClass.OTHER,
                        why=" ".join(line.why for line in lines),
                        block=handles[c.source],
                        at=tuple(line.at for line in lines),
                        evidence=tuple(line.evidence for line in lines),
                    )
                )
        elif c.op == "merge" and c.source in merged_into:
            n = merged_into.pop(c.source)
            held = found[n]
            found[n] = dataclasses.replace(
                held,
                why=f"{held.why} {c.why}",
                blocks=(*held.blocks, handles[c.target]),
                evidence=(*held.evidence, c.evidence),
            )
            merged_into[c.target] = n
        elif c.op == "merge":
            merged_into[c.target] = len(found)
            found.append(
                CutCorrection(
                    id="",
                    op=CutOp.MERGE,
                    cls=CorrectionClass.OTHER,
                    why=c.why,
                    blocks=(handles[c.source], handles[c.target]),
                    evidence=(c.evidence,),
                )
            )
        else:
            found.append(
                CutCorrection(
                    id="",
                    op=CutOp.REBOUND,
                    cls=CorrectionClass.OTHER,
                    why=c.why,
                    blocks=(handles[c.source], handles[c.target]),
                    at=(c.at,),
                    evidence=(c.evidence,),
                )
            )
    return _decisions(tuple(found), sheet, handles)


def _decisions(
    found: tuple[CutCorrection, ...], sheet: es.Sheet, handles: dict[str, str]
) -> tuple[CutCorrection, ...]:
    """A merge of every block of a group comes after the group's `ungroup`,
    the two one decision, so a merge never leaves a group of one block."""
    out: list[CutCorrection] = []
    decisions = 0
    for c in found:
        whole = [
            g
            for g, members in sheet.groups.items()
            if c.op is CutOp.MERGE
            and members
            and {handles[m] for m in members} <= set(c.blocks)
        ]
        if whole:
            decisions += 1
            tag = f"d{decisions}"
            out += [
                CutCorrection(
                    id="",
                    op=CutOp.UNGROUP,
                    cls=CorrectionClass.OTHER,
                    why=f"its blocks merged: {c.why}",
                    group=handles[g],
                    decision=tag,
                )
                for g in whole
            ]
            c = dataclasses.replace(c, decision=tag)
        out.append(c)
    return tuple(out)


# --- the edges' role correction -------------------------------------------

#: Why a support line on a block with no claim role corrected its roles.
CLAIM_ADDED_WHY = (
    "claim added by edges: a standing written on a block whose roles held no claim"
)
#: ... where the claim stands beside the block's roles, unsegmented;
#: an unplaced part's why adds its reason after it.
CLAIM_BESIDE_WHY = (
    f"{CLAIM_ADDED_WHY}; the claim stands beside the block's roles, unsegmented"
)


#: Why a standing on a block with no claim role added the claim beside the
#: block's roles, unsegmented: its part could not be placed (a `role` flag's
#: note).
CLAIM_NOT_PLACED = (
    "a standing here adds the claim role beside the block's roles, but its part "
    "cannot be placed"
)


class ClaimNotPlaced(ValueError):
    """A claim part whose start or end no excerpt places once in its block."""


def claims_added(
    sheet_text: str,
    handles: dict[str, str],
    document: Document,
    cut: Cut,
    *,
    person: bool = False,
) -> tuple[tuple[CutCorrection, ...], tuple[Flag, ...]]:
    """The role corrections a sheet's support lines imply: one
    `ROLES` correction per block whose roles hold no claim and which takes
    a support line, in the sheet's order, its claim part placed by
    `claim_part` from each such line, its evidence the lines' quoted words,
    ids left for the resolver. Every such standing is admitted: a
    block whose part cannot be placed (no excerpt stands once there, or its
    segments cannot be shown, or any other exception in placing it, named
    by its type) takes the claim beside its roles, unsegmented
    (`claim_beside`), and gives a `role` flag too, its words the first
    standing's and its note why, so a later step may place the part.
    A correction whose claim stands beside says so in its `why`
    (`CLAIM_BESIDE_WHY`), an unplaced part's reason after it. On a
    `person`'s answer, which is not paid for, a fault other than a
    `ValueError` is raised, never kept."""
    found: list[CutCorrection] = []
    raised: list[Flag] = []
    for handle, lines in es.standings_without_claim(es.parse(sheet_text)).items():
        block = cut.block(handles[handle])
        claims = [line.claim for line in lines]
        unplaced = ""
        try:
            roles, segments, used = claim_part(document, block, claims)
        except Exception as error:  # noqa: BLE001 - a paid answer is never lost
            # ClaimNotPlaced and a part's RenderError are expected; anything
            # else is a fault here, named by its type, and a model's answer
            # stands
            if person and not isinstance(error, ValueError):
                raise
            named = "" if isinstance(error, ValueError) else f"{type(error).__name__}: "
            unplaced = f": its part not placed: {named}{error}"
            raised.append(
                Flag(
                    id="",
                    answer=None,
                    blocks=(block.id,),
                    signal=Signal.ROLE,
                    evidence=(lines[0].claim,),
                    note=f"{CLAIM_NOT_PLACED}: {named}{error}",
                )
            )
            (roles, segments), used = claim_beside(block), tuple(claims)
        beside = len(roles) > 1 and not segments
        found.append(
            CutCorrection(
                id="",
                op=CutOp.ROLES,
                cls=CorrectionClass.OTHER,
                why=f"{CLAIM_BESIDE_WHY}{unplaced}"
                if beside or unplaced
                else CLAIM_ADDED_WHY,
                block=block.id,
                roles=roles,
                segments=segments,
                evidence=used,
            )
        )
    return tuple(found), tuple(raised)


def claim_beside(block: Block) -> tuple[tuple[Role, ...], tuple[Segment, ...]]:
    """A block's roles with the claim role beside them, unsegmented: one span
    may play two roles, and a segment holds one, so the block's
    placed boundaries go. A block with no role takes the claim
    whole, as one segment."""
    roles = role_set((*block.roles, Role.CLAIM))
    return roles, (() if len(roles) > 1 else (Segment("", Role.CLAIM),))


def claim_part(
    document: Document, block: Block, claims: Sequence[str]
) -> tuple[tuple[Role, ...], tuple[Segment, ...], tuple[str, ...]]:
    """A block's roles and segments with a claim part added for each of
    `claims`, its standings' quoted words, and the words that placed one. The part is
    sentence-based at both ends: it starts at the start of the sentence that
    holds the words' first stand in the block
    (`_claim_start`), never before the segment they stand in, and ends at
    the first sentence that starts at or after their end, or at the next
    segment's start if that comes first. The segment the claim falls in
    keeps its role before it and takes it up again after it, where text
    remains, not markup alone; every other segment stands as the roles
    gave it. A correction adds a role and never removes one:
    where the parts would leave one of the block's roles no text, and on a
    mixed block whose parts are not placed, the claim stands beside the
    block's roles, unsegmented (`claim_beside`). Words that stand inside a
    claim part already add none. Raises `ClaimNotPlaced` where no excerpt
    places a new start once in the block's owned text."""
    if len(block.roles) > 1 and not block.segments:
        return *claim_beside(block), tuple(claims)
    text = document.file(block.extent.file).text
    shut = _set_apart(text, block)
    first = block.owned[0].start
    while first < block.owned[0].end - 1 and text[first].isspace():
        first += 1
    # (offset, role, the segment's own start, None for a new one)
    bounds: list[tuple[int, Role | None, str | None]] = [
        (first, block.segments[0].role if block.segments else None, "")
    ]
    bounds += [
        (place.offset, segment.role, segment.starts)
        for segment, place in zip(
            block.segments[1:], part_lines(document, block), strict=True
        )
    ]
    used: list[str] = []
    for claim in claims:
        found = _first_place(document, block, claim)
        if found is None:
            continue  # the gate places every quote; none placed is refused below
        at, end = found
        at = max(at, first)
        floor, enclosing, starts = [b for b in bounds if b[0] <= at][-1]
        if enclosing is Role.CLAIM:
            continue
        start = _claim_start(text, block, shut, floor, at)
        later = [b[0] for b in bounds if b[0] > at]
        limit = later[0] if later else block.owned[-1].end
        resume = _sentence_start(text, block, shut, end, limit)
        if resume is not None and _markup_only(_owned_text(text, block, resume, limit)):
            resume = None
        bounds = [b for b in bounds if b[0] != floor]
        if start == floor:
            bounds.append((floor, Role.CLAIM, starts))
        else:
            bounds += [(floor, enclosing, starts), (start, Role.CLAIM, None)]
        if resume is not None:
            bounds.append((resume, enclosing, None))
        bounds.sort(key=lambda b: b[0])
        used.append(claim)
    if not used:
        raise ClaimNotPlaced("the claim's words stand in none of the block's runs")
    merged = [bounds[0]]
    for bound in bounds[1:]:
        if bound[1] is not merged[-1][1]:
            merged.append(bound)
    segments = tuple(
        Segment(
            "" if n == 0 else starts if starts else _excerpt(document, block, offset),
            role,
        )
        for n, (offset, role, starts) in enumerate(merged)
    )
    roles = role_set(s.role for s in segments if s.role is not None)
    if not set(block.roles) <= set(roles):
        return *claim_beside(block), tuple(claims)
    if len(segments) == 1:
        segments = (Segment("", segments[0].role),)
    problem = roles_problem(roles, segments)
    if problem is not None:
        raise ClaimNotPlaced(
            f"the claim's part does not make a block's roles: {problem}"
        )
    return roles, segments, tuple(used)


def _claim_start(
    text: str, block: Block, shut: Sequence[tuple[int, int]], floor: int, at: int
) -> int:
    """Where a claim part whose words first stand at `at` starts: the start
    of the sentence that holds `at`, the last sentence start at or before it
    (`_starts_sentence`, the end's own rule, so a display or a list belongs
    to the sentence it is set in), never before `floor`, the start of the
    segment it stands in; at `floor` where only markup lies between: the
    quote shows which sentence claims, not where the claim begins."""
    start = next(
        (x for x in range(at, floor, -1) if _starts_sentence(text, block, shut, x)),
        floor,
    )
    if _markup_only(_owned_text(text, block, floor, start)):
        return floor
    return start


def _first_place(
    document: Document, block: Block, words: str
) -> tuple[int, int] | None:
    """Where `words` first stand within one of the block's owned runs, as
    the sheet's check finds them (`edge_sheet.quoted`), as offsets in its
    file."""
    found = words.split()
    if not found:
        return None
    pattern = re.compile(r"\s+".join(re.escape(w) for w in found))
    for run in block.owned:
        m = pattern.search(document.text(run))
        if m:
            return run.start + m.start(), run.start + m.end()
    return None


#: LaTeX markup that carries no words of the block's own: a command with
#: its optional and brace arguments, and display delimiters.
_MARKUP = re.compile(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})*|\$\$|\\\[|\\\]")
#: What is set apart from the running sentences: a display (an equation
#: environment, `\[…\]`, `$$…$$`) or a list, delimiters included. No
#: sentence starts or ends inside one: it belongs to the sentence it is
#: set in.
_SET_APART = re.compile(
    r"\\begin\{(equation|eqnarray|align|alignat|gather|multline|flalign|"
    r"displaymath|math|itemize|enumerate|description)(\*?)\}.*?\\end\{\1\2\}"
    r"|\\\[.*?\\\]|\$\$.*?\$\$",
    re.DOTALL,
)


def _set_apart(text: str, block: Block) -> list[tuple[int, int]]:
    """The spans of the block's extent set apart (`_SET_APART`), as offsets
    in its file."""
    start, end = block.extent.start, block.extent.end
    return [
        (start + m.start(), start + m.end())
        for m in _SET_APART.finditer(text[start:end])
    ]


def _owned_text(text: str, block: Block, start: int, end: int) -> str:
    """The block's owned text from `start` to `end`, its runs joined."""
    return " ".join(
        text[max(run.start, start) : min(run.end, end)]
        for run in block.owned
        if run.start < end and start < run.end
    )


def _markup_only(text: str) -> bool:
    """Whether `text` holds no words besides markup (`_MARKUP`)."""
    return not _MARKUP.sub("", text).strip()


def _starts_sentence(
    text: str, block: Block, shut: Sequence[tuple[int, int]], at: int
) -> bool:
    """Whether a sentence starts at `at` in the block's owned text: a
    character neither a space nor in lower case, after a space, where the
    text before the space ends in `.`, `!` or `?` with any closing brackets
    and quotes after it (the cutter's sentence end, `cutter.blocks`), but
    not the full stop of an abbreviation (`_abbreviated`: "e.g. $x$", "p.
    \\pageref"); and neither it nor that end inside a span set apart
    (`_set_apart`), so a display's or a list's full stop ends no sentence
    and its closing `\\]` or `\\end{…}` starts none."""
    if at < 1 or text[at].isspace() or text[at].islower():
        return False
    if not text[at - 1].isspace():
        return False
    if not any(run.start <= at < run.end for run in block.owned):
        return False
    back = at - 1
    while back > 0 and text[back].isspace():
        back -= 1
    while back > 0 and text[back] in SENTENCE_CLOSERS:
        back -= 1
    if text[back] not in SENTENCE_ENDS or _abbreviated(text, back):
        return False
    return not any(s <= x < e for s, e in shut for x in (at, back))


#: The abbreviations whose full stop ends no sentence, lower case, their
#: inner stops left out ("e.g." is `eg`); a single letter is one too (an
#: initial, "p" in "p. 3").
_ABBREVIATIONS = frozenset(
    {
        "eg",
        "ie",
        "p",
        "pp",
        "cf",
        "vs",
        "etc",
        "resp",
        "fig",
        "sec",
        "eq",
        "thm",
        "def",
        "lem",
        "al",
    }
)


def _abbreviated(text: str, stop: int) -> bool:
    """Whether the full stop at `stop` closes an abbreviation
    (`_ABBREVIATIONS`) or a single letter: the word before it, its own
    inner stops included, read back to a space or a bracket."""
    if text[stop] != ".":
        return False
    begin = stop
    while begin > 0 and (text[begin - 1].isalpha() or text[begin - 1] == "."):
        begin -= 1
    word = text[begin:stop].replace(".", "").lower()
    return len(word) == 1 or word in _ABBREVIATIONS


def _sentence_start(
    text: str,
    block: Block,
    shut: Sequence[tuple[int, int]],
    start: int,
    limit: int,
) -> int | None:
    """The first offset from `start` and before `limit` where a sentence
    starts (`_starts_sentence`), else None."""
    return next(
        (
            at
            for at in range(max(start, 1), limit)
            if _starts_sentence(text, block, shut, at)
        ),
        None,
    )


def _excerpt(document: Document, block: Block, offset: int) -> str:
    """The fewest words from `offset`, three or more, that match once in the
    block's extent starting on a character it owns, as the assembler places
    a segment's start (`cut_file.excerpt_at`)."""
    text = document.file(block.extent.file).text
    start, end = block.extent.start, block.extent.end
    words = text[offset:end].split()
    for n in range(min(EXCERPT_WORDS, len(words)), len(words) + 1):
        starts = [
            start + s
            for s, _ in occurrences(text[start:end], " ".join(words[:n]))
            if any(run.start <= start + s < run.end for run in block.owned)
        ]
        if len(starts) == 1:
            return " ".join(words[:n])
    raise ClaimNotPlaced(
        f"no excerpt from {' '.join(words[:EXCERPT_WORDS])!r} stands once in "
        "the block: its claim part cannot be placed"
    )


def flags(sheet_text: str, handles: dict[str, str]) -> tuple[Flag, ...]:
    """A sheet's flag lines as flags, in the sheet's order, ids left for the
    resolver."""
    return tuple(
        Flag(
            id="",
            answer=None,
            blocks=(handles[f.source],),
            signal=Signal(f.signal or Signal.OTHER),
            evidence=(f.evidence,),
            note=f.note,
        )
        for f in es.parse(sheet_text).flag_lines
    )


def check(question: Question, text: str, document: Document) -> Validation:
    """An edited `edges.md`, the first pass's `lines.md` laid into the
    sheet, or a later pass's applied to the sheet it was shown (either
    sheet kept): its problems, or its records, the cut
    corrections it proposes, then the role corrections its standings imply
    (`claims_added`), and its flags."""
    assert question.workspace is not None and question.workspace.gate is not None
    assert question.input_cut is not None
    spec = json.loads(question.workspace.gate.spec)
    found = spec_problems(spec, text, document)
    if found:
        return Validation(problems=tuple(found))
    kept = None
    if CHANGES in spec:
        text = kept = applied_changes(spec[CHANGES], text)
    elif spec.get(LINES):
        text = kept = laid(spec["reference"], text)
    handles = dict(question.handles)
    cut = question.input_cut
    applied: tuple[CutCorrection, ...] = ()
    written: tuple[CutCorrection, ...] = ()
    unwritten: tuple[Flag, ...] = ()
    if PLAIN in spec:
        # an update's one session: on the cut its queue's applied lines left
        view = plain_view(spec, text, document)
        handles, cut, applied = dict(view.handles), view.cut or cut, view.applied
        written, unwritten = roles_written(
            text, handles, document, cut, view.reference, view.roles_open
        )
    person = bool(spec.get(PERSON_SPEC))
    added, unplaced = claims_added(text, handles, document, cut, person=person)
    if person:
        unplaced = ()  # a person's answer holds no flag; its `why` says it
    return Validation(
        corrections=(*applied, *proposals(text, handles), *added, *written),
        edges=records(
            text,
            handles,
            deltas="update" in spec
            or PLAIN in spec
            or bool((spec.get(BESIDE) or {}).get("deltas")),
        ),
        flags=(*flags(text, handles), *unplaced, *unwritten),
        kept=kept,
    )


def spec_problems(
    spec: dict[str, Any], edited: str, document: Document | None = None
) -> list[str]:
    """The gate's problems with `edited` under `spec`, a person's keep mark
    refused where the spec does not take it; lines by owner laid into the
    sheet first where the spec says so (`LINES`)."""
    if CHANGES in spec:
        return _changes_problems(spec, edited, document)
    if spec.get(LINES):
        return _lines_problems(spec, edited, document)
    found = _sheet_problems(spec, edited, document)
    if BESIDE not in spec:
        # a model's sheet, or one shown nothing beside: no stale mark is its
        found += [
            f"{owner}: a stale mark is the store's, shown, never written: remove "
            f"`{line}`"
            for owner, line in es.parse(edited).marks
        ]
    if not spec.get(KEEPS):
        found += [
            f"{owner}: the keep mark `{es.KEEP_MARK.strip()}` is a person's: remove "
            f"it from `{line}`"
            for owner, line in es.parse(edited).kept
        ]
    return unkept(found, spec.get(KEPT_PROBLEMS, ()))


# --- a fragment of sheet lines ---------------------------------------------------

#: A fragment's line operations: add, retract, keep.
ADD, RETRACT, KEEP = "+", "-", "="


class FragmentError(ValueError):
    """A fragment a sheet cannot take, with why."""


@dataclass(frozen=True, slots=True)
class Fragment:
    """A fragment of sheet lines: legend lines to add, and per owner (a
    handle) its lines, each with its operation, in order."""

    legend: tuple[str, ...] = ()
    lines: tuple[tuple[str, str, str], ...] = field(default=())


def restates_a_claim(sheet: es.Sheet, m: re.Match[str]) -> bool:
    """Whether an about line points its owner at claims the paper asserts:
    the claims of its target it names, none where it names only parts,
    else every claim its target holds (`docs/model.md` § 4), each standing
    other than `conjectured`. A summary of a conjecture asserts what the
    paper only conjectures: it stands nothing restated."""
    held = es.standings_by_handle(sheet, m.group("target") or "")
    named = [h.strip() for h in (m.group("parts") or "").split(",") if h.strip()]
    claims = [held[h] for h in named if h in held] if named else list(held.values())
    return bool(claims) and all(s.status != Status.CONJECTURED for s in claims)


#: The about flags that describe the target, never restate it.
_DESCRIBES = ("where-used", "generalizes")


def argued_by(
    sheet: es.Sheet, owner: str, line: str, restating: bool = False
) -> tuple[str, Status, list[str]]:
    """The claims an edge line `owner` writes argues, as a block and the
    standing it gives them: a justifies line its target's, `justified`; a
    self mark its block's, `self`; each the claims it names, else its
    block's one claim, a line naming none of a block's several being a
    fallback that argues none of them (`docs/model.md` § 4). An about line
    onto a claim the paper asserts (`restates_a_claim`), `restated`: the
    claims its source end names; an about line is mostly a description,
    so one naming none restates its block's one claim only where its
    author writes it as a restatement (`restating`). No claim for any
    other line."""
    m = es.EDGE.match(line)
    if m is None:
        return owner, Status.UNSUPPORTED, []
    kind = m.group("kind")
    flags = (m.group("flag") or "").split()
    if kind == EdgeKind.JUSTIFIES and m.group("target"):
        block, status, bracket = m.group("target"), Status.JUSTIFIED, "parts"
    elif kind == es.SELF:
        block, status, bracket = owner, Status.SELF, "own"
    elif (
        kind == EdgeKind.ABOUT
        and not any(f in flags for f in _DESCRIBES)
        and restates_a_claim(sheet, m)
    ):
        block, status, bracket = owner, Status.RESTATED, "own"
    else:
        return owner, Status.UNSUPPORTED, []
    named = [h.strip() for h in (m.group(bracket) or "").split(",") if h.strip()]
    if es.RESTATES_NONE in named:
        return block, status, []
    held = es.standings_by_handle(sheet, block)
    claims = [h for h in named if h in held]
    if claims or (status is Status.RESTATED and not restating):
        return block, status, claims
    return block, status, list(held) if len(held) == 1 else []


def stood_argued(
    sheet: str, fragment: Fragment, restating: bool = False
) -> list[tuple[str, str, str]]:
    """The ⊢ lines the fragment's `+` lines rewrite, so a line and its
    claim's standing never disagree: a claim standing `unsupported` that
    a justifies, self or about line it adds argues (`argued_by`, its
    author `restating` or not) stands `justified`, `self` or `restated`,
    its ⊢ line taken off and written anew, its declaration and note
    dropped, its handle kept where its block holds several claims. A line
    that gives back one the fragment takes off (same owner, kind and
    target) stands only the claims the line taken off did not argue: a
    line rewritten or narrowed lends no new support. A claim whose
    standing the fragment sets itself keeps the fragment's. Refused
    (`FragmentError`) where a person kept the standing `unsupported` and
    the fragment does not set it."""
    parsed = es.parse(sheet)
    rows = sheet.split("\n")
    set_here = {
        (o, es.norm(s.group("claim")))
        for o, _, x in fragment.lines
        if (s := es.SUPPORT.match(x))
    }

    def same(owner: str, line: str) -> tuple[str, str, str] | None:
        m = es.EDGE.match(line)
        return None if m is None else (owner, m.group("kind"), m.group("target") or "")

    # what a line taken off argued, read as the sheet holds it: an about
    # line there naming no claim is a description where its claim stands
    # unsupported
    taken: dict[tuple[str, str, str], set[str]] = {}
    for owner, op, line in fragment.lines:
        if op == RETRACT and (key := same(owner, line)) is not None:
            taken.setdefault(key, set()).update(argued_by(parsed, owner, line)[2])
    found: list[tuple[str, str, str]] = []
    for owner, op, line in fragment.lines:
        if op != ADD:
            continue
        block, status, claims = argued_by(parsed, owner, line, restating)
        before = taken.get(same(owner, line) or ("", "", ""), set())
        held = es.standings_by_handle(parsed, block)
        for h in claims:
            s = held[h]
            if (
                h in before
                or s.status != Status.UNSUPPORTED
                or (block, es.norm(s.claim)) in set_here
            ):
                continue
            was, kept = es.unkept(rows[s.line - 1])
            if kept:
                raise FragmentError(
                    f"⟦{owner}⟧ {line}: a person kept its standing on {block}'s "
                    f"claim [{h}] `unsupported`: leave it as written, or set the "
                    f'standing in the same edit (`⊢ {status.value} · "{s.claim}"`)'
                )
            handle = f" [{h}]" if len(held) > 1 else ""
            rewritten = [
                (block, RETRACT, was),
                (block, ADD, f'⊢ {status.value}{handle} · "{s.claim}"'),
            ]
            found += [x for x in rewritten if x not in found]
            set_here.add((block, es.norm(s.claim)))
    return found


def restating_none(sheet: str, fragment: Fragment) -> list[str]:
    """What a person's `+` about lines leave unsupported, said: an about
    line onto a claim the paper asserts that names no claim at its source
    end describes its target and restates nothing (`argued_by`), so where
    its owner holds claims standing `unsupported` that the fragment does
    not set, each such line is said with the line that would stand one
    restated, `→ about [<claim>] T`, and the standing to set instead. A
    standing a person kept `unsupported` is left out: a line naming it is
    refused (`stood_argued`)."""
    parsed = es.parse(sheet)
    rows = sheet.split("\n")
    set_here = {
        (o, es.norm(m.group("claim")))
        for o, _, x in fragment.lines
        if (m := es.SUPPORT.match(x))
    }
    found: list[str] = []
    for owner, op, line in fragment.lines:
        m = es.EDGE.match(line)
        if (
            op != ADD
            or m is None
            or m.group("kind") != EdgeKind.ABOUT
            or m.group("own")
            or any(f in (m.group("flag") or "").split() for f in _DESCRIBES)
            or not restates_a_claim(parsed, m)
        ):
            continue
        left = [
            (h, s)
            for h, s in es.standings_by_handle(parsed, owner).items()
            if s.status == Status.UNSUPPORTED
            and (owner, es.norm(s.claim)) not in set_here
            and not es.unkept(rows[s.line - 1])[1]
        ]
        if not left:
            continue
        h, s = left[0]
        named = line.replace("→ about ", f"→ about [{h}] ", 1)
        which = ", ".join(f"[{h}]" for h, _ in left)
        found.append(
            f"⟦{owner}⟧ {line}: names no claim at its source end, so it "
            f"restates none: {owner}'s claim {which} still stands `unsupported`. "
            f"Name the claim it restates to stand it restated, `{named}`; or set "
            f"its standing yourself, `--standing {owner} "
            f"'⊢ <status> · \"{s.claim}\"'`"
        )
    return found


def applied_fragment(sheet: str, fragment: Fragment, restating: bool = False) -> str:
    """`sheet` with the fragment applied: each legend line after the
    sheet's last legend line; under each owner (its flag lines among
    its own), each `+` line after its
    last line, each `-` line taken off and each `=` line marked kept; and
    the standings its `+` lines argue (`stood_argued`, its author
    `restating` or not). Refused (`FragmentError`), naming it, where an
    owner is not the sheet's, or a `-` or `=` line names no one line under
    its owner (`named_lines`), or a `+` line is there already."""
    fragment = Fragment(
        fragment.legend,
        (*fragment.lines, *stood_argued(sheet, fragment, restating)),
    )
    parsed = es.parse(sheet)
    lines = sheet.split("\n")
    owners = set(parsed.texts)
    for owner, _, _ in fragment.lines:
        if owner not in owners:
            raise FragmentError(f"⟦{owner}⟧ is no block or group of the sheet")
    written = written_lines(sheet, flags=True)
    resolved: list[tuple[str, str, str]] = []
    for owner, op, line in fragment.lines:
        held = written.get(owner, [])
        if op in (RETRACT, KEEP):
            found = named_lines(held, line)
            if len(found) != 1:
                raise FragmentError(_unnamed(owner, line, found, held))
            line = found[0]
        if op == ADD and line in held:
            raise FragmentError(f"⟦{owner}⟧ holds the line `{line}` already")
        resolved.append((owner, op, line))
    fragment = Fragment(fragment.legend, tuple(resolved))
    out: list[str] = []
    owner: str | None = None
    adds = {
        o: [x for o2, op, x in fragment.lines if o2 == o and op == ADD] for o in owners
    }
    takes = {(o, x) for o, op, x in fragment.lines if op == RETRACT}
    keeps = {(o, x) for o, op, x in fragment.lines if op == KEEP}

    def close() -> None:
        if owner is None or not adds.get(owner):
            return
        at = len(out)
        while at and (out[at - 1] == "" or es.DONE.match(out[at - 1])):
            at -= 1
        out[at:at] = adds[owner]

    for line in lines:
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            close()
            owner = None if es.GROUP.match(line) else m.group(1)
        elif owner is not None and (owner, line) in takes:
            continue
        elif owner is not None and (owner, line) in keeps:
            line = es.KEEP_MARK + line
        out.append(line)
    close()
    if fragment.legend:
        legend_at = [i for i, x in enumerate(out) if es.LEGEND.match(x)]
        files_at = next(i for i, x in enumerate(out) if es.FILES.match(x))
        at = legend_at[-1] + 1 if legend_at else files_at
        out[at:at] = list(fragment.legend)
    return "\n".join(out)


#: A standing as `show` prints it as well as the sheet: its claim's handle
#: there or left out, and its completeness after the quote where not
#: `given`.
_STANDING_SHOWN = re.compile(
    r"^⊢ (?P<status>\w+(?: [\w-]+)?)(?: \[[^\]\s]+\])?"
    r' · "(?P<claim>.*?)"(?: — .*| \([^()]*\))?$'
)


def _line_name(line: str) -> str:
    """What names a line: an edge's head and quote, a standing's status and
    claim; its ` — <why>` tail, a standing's handle and the completeness
    `show` ends it in aside. Any other line is named by itself."""
    if m := es.EDGE.match(line):
        return line[: m.end("evidence") + 1]
    if m := _STANDING_SHOWN.match(line):
        return f'⊢ {m["status"]} · "{m["claim"]}"'
    return line


def named_lines(held: Sequence[str], line: str) -> list[str]:
    """The lines of `held` that `line` names: the one it equals, or each
    with its head and quote (`_line_name`). A person copies a line from
    `show`, which ends a standing in its completeness, and may leave a
    standing's handle or the tail out."""
    if line in held:
        return [line]
    return [x for x in held if _line_name(x) == _line_name(line)]


def _unnamed(owner: str, line: str, found: list[str], held: Sequence[str]) -> str:
    """Why `line` names no one line under `owner`, with the lines it could
    mean, whole, to copy: those it names, or the owner's of its kind."""
    if found:
        head = (
            f"⟦{owner}⟧ holds {len(found)} lines with the head and quote of "
            f"`{line}`; give one whole:"
        )
    else:
        head = f"⟦{owner}⟧ holds no line `{line}`"
        found = [x for x in held if x.split(" ", 1)[0] == line.split(" ", 1)[0]]
        if not found:
            return head
        head += "; it holds:"
    return "\n".join([head, *(f"  {x}" for x in found)])


def answer_problems(spec: dict[str, Any], edited: str) -> list[str]:
    """What the edges gate refuses in `edited` under `spec`, a question's
    gate spec: its check, run outside the session (a review's gate runs
    it on the state's sheet with the review's edge lines applied)."""
    return spec_problems(spec, edited)


def _sheet_problems(
    spec: dict[str, Any], edited: str, document: Document | None
) -> list[str]:
    pins = spec.get("pins", ())
    shown = spec.get("shown")
    if "update" in spec:
        found = _update_problems(spec, edited)
        return found + pin_problems(pins, edited, found, shown)
    if PLAIN in spec:
        found = _plain_problems(spec, edited, document)
        return found + pin_problems(pins, edited, found, shown)
    beside = spec.get(BESIDE) or {}
    found = problems(
        spec["reference"],
        edited,
        spec["block_ids"],
        spec["files"],
        spec.get("shown"),
        spec.get("reopened"),
        spec.get("within"),
        # a shown Δ may name a part the edit removed
        gone=[(o, label) for o, label, _, _ in beside.get("deltas", []) if label],
        claims_fixed=spec.get(CLAIMS_FIXED, ()),
    )
    reopened = spec.get("reopened")
    found += group_line_problems(
        edited, None if reopened is None else set(reopened), pins
    )
    if BESIDE in spec:
        found += _beside_problems(spec, es.parse(edited))
    else:
        found += [
            f"line {d.line}: {d.source} Δ: a Δ line is an update's, not this "
            "pass's; remove it"
            for d in es.parse(edited).deltas
        ]
    if spec.get(PERSON_SPEC):
        sheet = es.parse(edited)
        found += [
            f"{c.source}: a cut line is not a person's in edges.md: correct the cut "
            "in cut.md (`axiomation sheet … --stage cut`)"
            for c in sheet.cuts
        ]
        found += [
            f"{f.source}: a `?` flag asks a later step, which a person's answer "
            "does not: remove it"
            for f in sheet.flag_lines
        ]
    return found + pin_problems(pins, edited, found, shown)


# --- the gate -----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """The session's gate, as Claude Code's hooks: `<spec> <edited>` when
    the session tries to end, `<spec> <edited> write` after each tool use.
    Nothing when the sheet reads, else Claude Code's `block` decision with
    its problems: at the end at most `gates.ROUNDS` times; on a write only
    when the sheet changed since the last check, and without what is
    merely unfinished. It never fails the session: a gate that cannot run
    lets it go on, and the runner's check decides."""
    gates.hook_input()
    args = list(argv if argv is not None else sys.argv[1:])
    try:
        spec_path, edited_path = Path(args[0]), Path(args[1])
        writing = args[2:3] == [WRITE]
        edited = gates.read_regular(edited_path)
        if writing and gates.unchanged(spec_path, edited):
            return 0
        spec = gates.spec(spec_path)
        found = spec_problems(spec, edited)
        if writing:
            found = [p for p in found if not p.startswith((*es.UNFINISHED, OWED))]
    except Exception as error:  # noqa: BLE001 - the gate must never trap a session
        print(f"the edges gate could not run: {error}", file=sys.stderr)
        return 0
    blocked = bool(found) and (writing or gates.round_left(spec_path))
    gates.log(
        spec_path,
        {
            "mode": WRITE if writing else "stop",
            "problems": list(found),
            "blocked": blocked,
        },
    )
    if blocked:
        then = "and go on" if writing else "then finish"
        gates.block(gates.reason(edited_path.name, found, then, SHOWN))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
