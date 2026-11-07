"""The deciding step: one session on `cut.md` over the cut the groups left.

It is the boundary form (`cut_file`) with more in front of it: each
block's roles in its marker, a `⟦+ <role>⟧` line where a later part
starts, the groups' lines (`groups_file.group_lines`), and the flags the
steps before it raised, `⟦? <step>: <signal> · "<words>" — <note>⟧`, at
the start of the block they are about. These lines are shown, never read,
and are part of the key.

Every edit carries the paper's words. A split is `⟦new⟧` or `⟦new —
<why>⟧`: its evidence is the words where the new block starts. A join
replaces the marker it removes with `⟦join · "<words of the block
before>" "<words of this block>" — <why>⟧`, which the check refuses
without both quotes, each verbatim in its block (whitespace aside), or
without a reason: a merge needs stronger evidence than a split. A moved marker is a
move: its reason may follow `—` in it, and its evidence is the words where
the block now starts; it must stand where
a new block can start, and its merge and split are one decision. A join
never crosses a group's edge; one that joins every block of a group is a
decision, the group's `ungroup` and the merge, applied together or not at
all.

An update calls the same builder on a run of blocks to re-cut, with its
neighbours as context and no flags.
"""

from __future__ import annotations

import json
import re
import sys
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from itertools import groupby
from pathlib import Path

from axiomation.cutter.model import Cut, Group
from axiomation.flags import Raised
from axiomation.questions import (
    Gate,
    Question,
    Stage,
    Template,
    Validation,
    gates,
)
from axiomation.questions.chunks import Chunk
from axiomation.questions.cut_file import (
    CONTEXT,
    CONTINUED,
    CUT_FILE,
    MARKER,
    OUTLINE_BRIEF,
    Boundaries,
    Insertion,
    ParseError,
    boundary_edit,
    chunk_questions,
    is_flag,
    places_shown,
    read_boundaries,
    read_markers,
    top_handles,
)
from axiomation.questions.cut_review import applied_alone
from axiomation.questions.groups_file import (
    END,
    GROUP,
    group_lines,
)
from axiomation.questions.roles_file import SEGMENT, part_lines
from axiomation.reader import Document
from axiomation.records import CorrectionClass, CutCorrection, CutOp

TEMPLATE = "cut-review-decide"
KICKOFF = "Decide the cut in cut.md, as your instructions say."
#: An update's re-cut of its changed regions: the region
#: as it read before, shown beside `cut.md`, bound in the key.
OLD_FILE = "old.md"
UPDATE_TEMPLATE = "cut-review-update"
UPDATE_KICKOFF = "Correct the cut in cut.md, as your instructions say."
#: The head of a join line.
JOIN = "join"
JOIN_FORM = '⟦join · "<words of the block before>" "<words of this block>" — <why>⟧'
_JOIN_LINE = re.compile(
    r'^join\s*·?\s*"(?P<before>[^"]*)"\s+"(?P<this>[^"]*)"\s*(?:—\s*(?P<why>.*))?$'
)
WHY = "the deciding step"


def decide_questions(
    document: Document,
    cut: Cut,
    found_chunks: Sequence[Chunk],
    template: Template,
    flags: Sequence[Raised] = (),
    with_outline: bool = False,
    old: Callable[[Chunk], str] | None = None,
    kickoff: str = KICKOFF,
) -> tuple[Question, ...]:
    """One session per chunk: the whole paper as one at the default chunk
    size, or a run of blocks with its neighbours as context. Each block's
    later parts, the groups that lie in the chunk and the flags on its
    blocks are shown and bound in the key;
    with `old` (an update's re-cut), the chunk as it read
    before too, as `old.md`."""
    instructions = template.render(
        **{name: text if with_outline else "" for name, text in OUTLINE_BRIEF.items()}
    )
    found: list[Question] = []
    for chunk in found_chunks:
        _, before, after = group_lines(document, cut, chunk)
        lines = [
            *(
                line
                for block_id in chunk.blocks
                for line in part_lines(document, cut.block(block_id))
            ),
            *_flag_lines(cut, chunk, flags),
        ]
        was = old(chunk) if old is not None else None
        asked = chunk_questions(
            document,
            cut,
            [chunk],
            template,
            instructions,
            kickoff,
            Stage.DECIDE,
            {"outline": with_outline, **({"old": was} if was is not None else {})},
            insertions=lines,
            with_outline=with_outline,
            before_lines=before,
            after_lines=after,
        )
        if was is not None:
            asked = tuple(
                replace(
                    q,
                    workspace=replace(
                        q.workspace, files={**q.workspace.files, OLD_FILE: was}
                    ),
                )
                for q in asked
                if q.workspace is not None
            )
        found += asked
    return tuple(_gated(question, document) for question in found)


def _gated(question: Question, document: Document) -> Question:
    """The question with its gate: its file read against what it
    showed each time the session tries to end, so a refused edit is fixed
    in the session rather than asked again. The gate is not in the key."""
    assert question.workspace is not None
    gate = Gate(module=__name__, spec=Shown.of(question, document).spec())
    return replace(question, workspace=replace(question.workspace, gate=gate))


def _flag_lines(cut: Cut, chunk: Chunk, flags: Sequence[Raised]) -> list[Insertion]:
    """A line for each flag on a block of the chunk, at the start of the
    first such block's text."""
    inside = set(chunk.blocks)
    found = []
    for raised in flags:
        held = [b for b in raised.flag.blocks if b in inside]
        if not held:
            continue
        block = cut.block(held[0])

        def clean(text: str) -> str:
            return " ".join(text.replace("⟧", "]").replace('"', "'").split())

        words = " ".join(f'"{clean(w)}"' for w in raised.flag.evidence)
        shown = f" · {words}" if words else ""
        line = (
            f"⟦? {raised.stage}: {raised.flag.signal.value}{shown} — "
            f"{clean(raised.flag.note)}⟧"
        )
        found.append(Insertion(block.extent.file, block.owned[0].start, line))
    return found


# --- the answer ---------------------------------------------------------------------


def _shown(tokens: tuple[str, ...]) -> bool:
    """A line the file shows and never reads: a part's start, a group's
    line, a flag."""
    if not tokens:
        return False
    head = tokens[0]
    return head in (SEGMENT, GROUP) or tokens[:2] == (END, GROUP) or is_flag(tokens)


_JOIN_HEAD = re.compile(rf"^{JOIN}\b", re.IGNORECASE)


def _is_join(tokens: tuple[str, ...]) -> bool:
    """A join line, or what was meant as one: any marker that starts with
    the word `join`, so a malformed one is reported, not read as a new
    block's marker."""
    return bool(tokens) and bool(_JOIN_HEAD.match(tokens[0]))


def _not_read(tokens: tuple[str, ...]) -> bool:
    """What the boundary reading leaves out: the shown lines, and the join
    lines, which this step reads apart."""
    return _shown(tokens) or _is_join(tokens)


def _reason(tokens: tuple[str, ...]) -> str:
    """What follows `—` in a marker: its reason."""
    if "—" not in tokens:
        return ""
    return " ".join(tokens[tokens.index("—") + 1 :]).strip()


@dataclass(frozen=True, slots=True)
class Shown:
    """What the deciding step's file shows, as plain data: what its gate
    reads the edit against, without the paper (its spec). `owners` is, for
    each non-whitespace character the file shows, the handle of the block
    that owns it; `top` the handles of the top-level blocks in order;
    `groups` the handles of each group of two or more blocks (`_groups`;
    a member outside the chunk keeps its id, never a handle, so no join
    holds that group whole); `named` each block a group names, with that
    group's index in `groups` (`None` for a group the file does not
    show): the assembler refuses a join that moves such a block's start."""

    reference: str
    owners: tuple[str, ...]
    top: tuple[str, ...]
    groups: tuple[tuple[str, ...], ...]
    named: tuple[tuple[str, int | None], ...] = ()

    @classmethod
    def of(cls, question: Question, document: Document) -> Shown:
        assert question.input_cut is not None and question.workspace is not None
        handle_of = {block: handle for handle, block in question.handles.items()}
        groups = _groups(question)
        shown_ids = [g.id for g in groups]
        return cls(
            reference=question.workspace.files[CUT_FILE],
            owners=tuple(
                handle_of[block] for _, _, block in places_shown(question, document)
            ),
            top=tuple(top_handles(question)),
            groups=tuple(tuple(handle_of.get(b, b) for b in g.blocks) for g in groups),
            named=tuple(
                (
                    handle_of[g.names],
                    shown_ids.index(g.id) if g.id in shown_ids else None,
                )
                for g in question.input_cut.groups
                if g.names in handle_of
            ),
        )

    def spec(self) -> str:
        return json.dumps(
            {
                "reference": self.reference,
                "owners": _run_lengths(self.owners),
                "top": list(self.top),
                "groups": [list(g) for g in self.groups],
                "named": [list(n) for n in self.named],
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_spec(cls, spec: str) -> Shown:
        data = json.loads(spec)
        return cls(
            reference=data["reference"],
            owners=tuple(h for h, count in data["owners"] for _ in range(count)),
            top=tuple(data["top"]),
            groups=tuple(tuple(g) for g in data["groups"]),
            named=tuple((h, n) for h, n in data["named"]),
        )


def _groups(question: Question) -> list[Group]:
    """The groups of two or more blocks the question's file shows a block
    of, in the cut's order: `Shown.groups`, and the ids `parse` ungroups
    by their index."""
    assert question.input_cut is not None
    shown = set(question.handles.values())
    return [
        g
        for g in question.input_cut.groups
        if len(g.blocks) > 1 and any(b in shown for b in g.blocks)
    ]


@dataclass(frozen=True, slots=True)
class _Run:
    """A run of joined blocks as the edit makes it: its handles, the words
    and reasons it carries (a moved marker's evidence by its place, whose
    words `parse` finds in the paper), the places of its moved markers
    (each also a split, one decision with the merge), and the groups it
    joins whole (by their index in `Shown.groups`)."""

    handles: tuple[str, ...]
    evidence: tuple[str | int, ...]
    whys: tuple[str, ...]
    moves: frozenset[int]
    whole: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _Decided:
    """The edit read from its text alone: the boundaries, the joined runs,
    and each marker's reason by its place."""

    boundaries: Boundaries
    runs: tuple[_Run, ...]
    reasons: dict[int, str]


def read_decision(shown: Shown, edited: str) -> _Decided:
    """The edit of the deciding step's file, read against what it showed,
    without the paper: what its gate checks and its answer is built from.
    Raises `ParseError` with what is wrong. Placing a new boundary by the
    paper's words is left to `parse`."""
    read = read_boundaries(shown.reference, edited, shown.top, shown_only=_not_read)
    found = read_markers(edited)
    texts: dict[str, list[str]] = {}
    for ch, handle in zip(
        read_markers(shown.reference).stream, shown.owners, strict=True
    ):
        texts.setdefault(handle, []).append(ch)

    def holds(handle: str, words: str) -> bool:
        squeezed = "".join(words.split())
        return bool(squeezed) and squeezed in "".join(texts.get(handle, []))

    headers = [at for _, at in read_markers(shown.reference).headers]
    problems = _shown_lines_kept(shown.reference, edited)
    joins = {}
    reasons: dict[int, str] = {}
    moved_to: dict[str, int] = {}
    for marker in found.markers:
        tokens = marker.tokens
        if not tokens or _shown(tokens):
            continue
        if _is_join(tokens):
            joins[marker.at] = marker
            continue
        if tokens[0] == CONTEXT or tokens[1:] == (CONTINUED,):
            continue
        reasons[marker.at] = _reason(tokens)
        if tokens[0] in read.moved:
            moved_to[tokens[0]] = marker.at
        if marker.at in read.news and not _cuttable(shown, marker.at):
            problems.append(
                f"line {marker.line}: {marker.text} stands in the context or "
                "past the last block, which is not yours to cut"
            )

    runs: list[_Run] = []
    for run in read.merges:
        moves: set[int] = set()
        evidence: list[str | int] = []
        whys: list[str] = []
        for before, this in zip(run, run[1:], strict=False):
            if this in read.moved:
                at = moved_to.get(this)
                if at is None or at not in read.news or not _cuttable(shown, at):
                    problems.append(
                        f"{this}'s marker moved where no block can start: a moved "
                        "marker stands inside the text where its block now "
                        f"starts; to join {this} to {before}, put a join line in "
                        f"its place, {JOIN_FORM}"
                    )
                    continue
                if read.head_of.get(shown.owners[at]) != run[0]:
                    problems.append(
                        f"{this}'s marker moved past another block's marker: move "
                        f"a marker only within the text of {before} and {this}, "
                        "and to join or split elsewhere, use a join line or "
                        "⟦new⟧"
                    )
                    continue
                moves.add(at)
                evidence.append(at)
                if reasons.get(at):
                    whys.append(reasons[at])
                continue
            marker = joins.pop(read.start[this], None)
            if marker is None:
                problems.append(
                    f"{this}'s marker was deleted: to join {this} to {before}, "
                    f"put a join line in its place, {JOIN_FORM}"
                )
                continue
            where = f"line {marker.line}: {marker.text}"
            inner = MARKER.match(marker.text)
            line = _JOIN_LINE.match(inner.group(1).strip()) if inner else None
            if line is None:
                problems.append(
                    f"{where} is not a join line, whose form is {JOIN_FORM}"
                )
                continue
            if not holds(before, line.group("before")):
                problems.append(
                    f"{where}: the first words in quotes are not {before}'s; quote "
                    "the block before from its text"
                )
            if not holds(this, line.group("this")):
                problems.append(
                    f"{where}: the second words in quotes are not {this}'s; quote "
                    "this block from its text"
                )
            why = (line.group("why") or "").strip()
            if not why:
                problems.append(
                    f"{where}: say why the blocks are one verdict, after ' — '"
                )
            evidence += [
                " ".join(line.group("before").split()),
                " ".join(line.group("this").split()),
            ]
            whys.append(why)
        held = set(run)
        crossed = [
            g
            for g in shown.groups
            if set(g) & held and not set(g) <= held and not held <= set(g)
        ]
        if crossed:
            problems.append(
                f"joining {', '.join(run)} crosses the edge of a group (from "
                f"{crossed[0][0]} to {crossed[0][-1]}): a join never crosses a "
                "group's line"
            )
            continue
        whole = tuple(n for n, g in enumerate(shown.groups) if set(g) <= held)
        moved_starts = [
            h for h, group in shown.named if h in run[1:] and group not in whole
        ]
        if moved_starts:
            problems.append(
                f"joining {', '.join(run)} moves the start of {moved_starts[0]}, "
                "which a group names (a proof its theorem, say): a block a "
                "group names is never joined to the block before it"
            )
            continue
        files = {_file_of(headers, read.start[h]) for h in run}
        if len(files) > 1:
            problems.append(
                f"joining {', '.join(run)} crosses a '##### <file>' line: blocks "
                "of two files are never joined"
            )
            continue
        runs.append(
            _Run(tuple(run), tuple(evidence), tuple(whys), frozenset(moves), whole)
        )
    for marker in joins.values():
        problems.append(
            f"line {marker.line}: {marker.text} stands where no block's marker "
            "was: put it in place of the marker of the block to join"
        )
    if problems:
        raise ParseError("\n".join(problems))
    return _Decided(read, tuple(runs), reasons)


def _cuttable(shown: Shown, at: int) -> bool:
    """Whether a block may start at `at`: within the file's text, in a block
    of the chunk, not its context."""
    return at < len(shown.owners) and shown.owners[at].startswith("b")


def _file_of(headers: Sequence[int], at: int) -> int:
    """Which `##### <file>` part of the file the place `at` lies in."""
    return bisect_right(headers, at)


def _shown_lines_kept(reference: str, edited: str) -> list[str]:
    """Where the lines the file shows and never reads (a part's start, a
    group's lines, a flag) were changed, moved or removed: each is told to
    put it back, since an edit of them is read as nothing."""

    def lines(text: str) -> Counter[tuple[str, int]]:
        return Counter(
            (" ".join(m.tokens), m.at)
            for m in read_markers(text).markers
            if _shown(m.tokens)
        )

    then, now = lines(reference), lines(edited)
    if then == now:
        return []
    gone = sorted(then - now, key=lambda line: line[1])
    added = sorted(now - then, key=lambda line: line[1])
    named = [f"⟦{text}⟧" for text, _ in gone] or [f"⟦{text}⟧" for text, _ in added]
    return [
        f"{named[0]} was changed, moved or removed: a part's, a group's and a "
        "flag's lines are shown, not read, and an edit of them changes nothing; "
        "put it back as it was"
    ]


def parse(question: Question, edited: str, document: Document) -> list[CutCorrection]:
    """The corrections the edit makes, with their evidence: each run of
    joined blocks a merge (after the `ungroup` of a group it joins whole,
    as one decision), then each block's splits. Raises `ParseError` with
    what is wrong."""
    shown = Shown.of(question, document)
    decided = read_decision(shown, edited)
    edit = boundary_edit(question, edited, document, shown_only=_not_read)
    excerpt_at = {
        at: excerpt for points in edit.splits.values() for at, excerpt in points
    }
    group_ids = [g.id for g in _groups(question)]
    corrections: list[CutCorrection] = []
    decisions = 0
    splits = dict(edit.splits)
    for run in decided.runs:
        evidence = [e if isinstance(e, str) else excerpt_at[e] for e in run.evidence]
        head = run.handles[0]
        # the move's own split joins its decision; the head's other new
        # boundaries stay splits of their own
        moved_split = [p for p in splits.get(head, []) if p[0] in run.moves]
        if moved_split:
            splits[head] = [p for p in splits[head] if p[0] not in run.moves]
            if not splits[head]:
                del splits[head]
        decision = None
        if run.whole or moved_split:
            decisions += 1
            decision = f"d{decisions}"
        corrections += [
            CutCorrection(
                id="",
                op=CutOp.UNGROUP,
                cls=CorrectionClass.OTHER,
                why=f"joined whole by the deciding step: {', '.join(run.handles)}",
                group=group_ids[n],
                decision=decision,
            )
            for n in run.whole
        ]
        corrections.append(
            CutCorrection(
                id="",
                op=CutOp.MERGE,
                cls=CorrectionClass.OTHER,
                why=" ".join(w for w in run.whys if w) or WHY,
                blocks=tuple(question.handles[h] for h in run.handles),
                evidence=tuple(evidence),
                decision=decision,
            )
        )
        if moved_split:
            corrections.append(
                _split(question, head, moved_split, decided.reasons, decision)
            )
    for block, points in splits.items():
        corrections.append(_split(question, block, points, decided.reasons))
    return corrections


def _split(
    question: Question,
    block: str,
    points: Sequence[tuple[int, str]],
    reasons: dict[int, str],
    decision: str | None = None,
) -> CutCorrection:
    """A block's splits, each at its place's excerpt, which is its evidence."""
    whys = [reasons[at] for at, _ in points if reasons.get(at)]
    return CutCorrection(
        id="",
        op=CutOp.SPLIT,
        cls=CorrectionClass.OTHER,
        why=" ".join(whys) or WHY,
        block=question.handles[block],
        at=tuple(excerpt for _, excerpt in points),
        evidence=tuple(excerpt for _, excerpt in points),
        decision=decision,
    )


def check(question: Question, text: str, document: Document) -> Validation:
    """An edited `cut.md` of the deciding step, parsed, and its corrections
    applied alone to the question's cut."""
    try:
        corrections = parse(question, text, document)
    except ParseError as error:
        return Validation(problems=tuple(str(error).split("\n")))
    return applied_alone(question, corrections, document)


# --- the gate -----------------------------------------------------------------------


def _run_lengths(owners: Sequence[str]) -> list[tuple[str, int]]:
    """`owners` as runs of one handle: the gate's spec, kept small."""
    return [(handle, len(list(run))) for handle, run in groupby(owners)]


def main(argv: Sequence[str] | None = None) -> int:
    """The session's gate, as Claude Code's Stop hook: nothing when the
    edited file reads, else Claude Code's `block` decision with its
    problems, at most `gates.ROUNDS` times. It never fails the session: a
    gate that cannot run lets it end, and the runner's check decides."""
    gates.hook_input()
    try:
        spec_path, edited_path = map(Path, argv if argv is not None else sys.argv[1:3])
        shown = Shown.from_spec(gates.read_regular(spec_path))
        read_decision(shown, gates.read_regular(edited_path))
    except ParseError as error:
        problems = str(error).split("\n")
    except Exception as error:  # noqa: BLE001 - the gate must never trap a session
        print(f"the deciding step's gate could not run: {error}", file=sys.stderr)
        return 0
    else:
        return 0
    if gates.round_left(spec_path):
        gates.block(gates.reason(edited_path.name, problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
