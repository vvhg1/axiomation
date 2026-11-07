"""The groups as a session on one file, per chunk.

A chunk is written out as `groups.md`, the cut paper as the roles review
shows it (`roles_file`): each block under its marker with the role of its
first part, `⟦b12 claim⟧`, and a `⟦+ <role>⟧` line where a later part
starts; the blocks around it under `⟦context⟧`, for reading. Each of the
cut's groups that lies in the chunk stands in place: a line `⟦group
<kind> <name>⟧` just before its first block's marker, and `⟦end group
<name>⟧` just after its last block's text. The name is one snake_case
word, unique in the file, so a nested group's lines cannot pair silently
with another's (a model can just pick a name in snake
case); the render names the cutter's groups by their first words.
Nothing checks a name against where it stands, so moving a line is one
edit. The session adds, removes and moves
groups by editing those lines alone; the text and every other line are
fixed. What a group justifies is an edge, not the group's, so a group is read from its
own run of blocks and the stage chunks as the others do. A cutter's group
that reaches into
the context, or holds one block, is not shown, and stays as proposed; a
group holds two or more blocks.

A build's and an update's sessions read the file as `paper.md` and answer
as lines (`front_lines`), every group the paper has, `group proof
bound_proof b13 b14`; the lines are laid into the file in place of the
group lines shown. A person's edit is of the file itself.

The answer is the edited `groups.md`, or the lines laid into it. Its group
lines are read (`read_groups`), paired by name, and compared with the
groups the file showed, by kind and where they stand: lines unchanged are
no correction; a group gone is an `ungroup`; any other is a `group`, after
the `ungroup`s. An answer is used whole or not at all.

Under a block's marker the session may add a flag line, `⟦? <signal>
"<the block's words>" — <note>⟧`: a position on the cut or an earlier
step, which changes nothing and a later step reads.

`read_groups` needs only the file as shown, the edited file and the block
each character of text belongs to, so the host runs it as the session's
gate (`main`), outside the session's sandbox.

The key is the kind, the stage, the template, the chunk's blocks as the
file shows them, with their roles, and the groups shown; the context
blocks are context.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import groupby
from pathlib import Path
from typing import Any

from axiomation.assembler import owned_runs
from axiomation.cutter.model import Cut, Group, GroupKind
from axiomation.questions import (
    Gate,
    Question,
    Stage,
    Template,
    Validation,
    Workspace,
    front_lines,
    gates,
)
from axiomation.questions.chunks import DEFAULT_OVERLAP, Chunk, chunks
from axiomation.questions.cut_file import (
    CONTEXT,
    CONTINUED,
    HEADER,
    MARKER,
    FlagLine,
    ParseError,
    RenderError,
    as_flags,
    block_inputs,
    fixed_lines,
    flag_lines,
    is_flag,
    places,
    read_markers,
    render,
    shown_runs,
    text_kept,
)
from axiomation.questions.cut_review import (
    applied_alone,
    labels_by_block,
    question_key,
)
from axiomation.questions.roles_file import NONE, first_role, part_lines
from axiomation.reader import Document
from axiomation.records import NONE as NAMES_NONE
from axiomation.records import STAGE_SIGNALS, CorrectionClass, CutCorrection, CutOp

KIND = "groups"
#: A session's brief, answered as lines; a person's file's.
SESSION_TEMPLATE = "groups-lines"
FILE_TEMPLATE = "groups-file"
GROUPS_FILE = "groups.md"
KICKOFF = "Correct the groups in groups.md, as your instructions say."
LINES_KICKOFF = "Write every group of paper.md in groups.md, as your instructions say."
SESSION_WHY = "the groups session"
SINGLE_WHY = "a group holds two or more blocks: the cut's one-block group is dropped"

GROUP = "group"
END = "end"

#: The kinds a session may mark, as its brief names them; the cutter
#: proposes no other.
KINDS = (GroupKind.PROOF, GroupKind.PROOF_SECTION, GroupKind.DERIVATION)

_START = re.compile(r"^group (\S+) (\S+)$")
_END = re.compile(r"^end group (\S+)$")
_FORM = "⟦group <kind> <name>⟧ or ⟦end group <name>⟧"

#: A cutter's group is named by this many of its first words, more where
#: two would share a name.
NAME_WORDS = 4
_COMMAND = re.compile(r"\\[A-Za-z]+\*?(\{[^}]*\})?")
_WORD = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class GroupSpan:
    """A group as the file marks it: its kind; its first and last blocks'
    handles; where its two lines stand, as the characters of text before
    each; and their line numbers."""

    kind: GroupKind
    first: str
    last: str
    start: int
    end: int
    line: int
    end_line: int

    @property
    def place(self) -> tuple[GroupKind, int, int]:
        return self.kind, self.start, self.end


def start_line(kind: GroupKind, name: str) -> str:
    return f"⟦{GROUP} {kind.value} {name}⟧"


def end_line(name: str) -> str:
    return f"⟦{END} {GROUP} {name}⟧"


def _key(name: str) -> str:
    """A name as lines are paired by it: case aside, `-` as `_`."""
    return name.casefold().replace("-", "_")


def _is_group(tokens: tuple[str, ...]) -> bool:
    words = [t.rstrip(":") for t in tokens[:2]]
    return bool(words) and (words[0] == GROUP or words == [END, GROUP])


def _in_chunk(handle: str) -> bool:
    """A chunk's block (`b…`), not one of its context (`c…`)."""
    return handle.startswith("b")


def _order(handle: str) -> tuple[str, int]:
    return handle[0], int(handle[1:])


# --- the questions ------------------------------------------------------------------


def _owners(document: Document, cut: Cut, handles: dict[str, str]) -> list[str]:
    """The handle of the block each non-whitespace character of text in
    the file belongs to, in order."""
    return [handles[block] for _, _, block in places(document, cut, handles)]


def session_questions(
    document: Document,
    cut: Cut,
    budget: int | None,
    template: Template,
    overlap: int = DEFAULT_OVERLAP,
    pass_: int = 1,
    lines: bool = False,
) -> tuple[Question, ...]:
    """One session per chunk, in reading order, as the boundary and roles
    stages chunk the paper, on the cut the roles left. A later pass
    (`pass_` 2 or more) corrects the groups the passes before it left; its
    pass is part of its key, so a pass that changed nothing is not asked
    again as the next. With `lines`, a session reads the file as
    `paper.md` and answers as lines (`front_lines`)."""
    labels = labels_by_block(document, cut)
    return tuple(
        _question(document, cut, chunk, template, labels, pass_, lines)
        for chunk in chunks(document, cut, budget, overlap)
    )


def _question(
    document: Document,
    cut: Cut,
    chunk: Chunk,
    template: Template,
    labels: dict[str, tuple[str, ...]],
    pass_: int = 1,
    lines: bool = False,
) -> Question:
    handles = {block: f"b{n}" for n, block in enumerate(chunk.blocks, 1)}
    context = {
        block: f"c{n}" for n, block in enumerate([*chunk.before, *chunk.after], 1)
    }
    inside = set(chunk.blocks)
    ordered, before, after = group_lines(document, cut, chunk)
    rendered = render(
        document,
        cut,
        chunk,
        handles,
        labels,
        insertions=[
            line
            for block_id in chunk.blocks
            for line in part_lines(document, cut.block(block_id))
        ],
        label=first_role,
        before=before,
        after=after,
    )
    owners = _owners(document, cut, {**handles, **context})
    try:
        shown = read_groups(rendered, rendered, owners)
    except ParseError as error:
        raise RenderError(
            f"the cut's groups cannot be shown in {GROUPS_FILE}: {error}"
        ) from None
    assert [s.kind for s in shown] == [g.kind for g in ordered]

    inputs = []
    for block_id in chunk.blocks:
        block = cut.block(block_id)
        entry = block_inputs(document, cut, block, handles[block_id], handles, labels)
        entry["segments"] = [
            {"starts": s.starts, "role": s.role.value if s.role else NONE}
            for s in block.segments
        ]
        inputs.append(entry)
    groups = [{"kind": s.kind.value, "first": s.first, "last": s.last} for s in shown]
    key_inputs: dict[str, Any] = {"blocks": inputs, "groups": groups}
    dropped = [
        {"kind": g.kind.value, "block": handles[g.blocks[0]]}
        for g in _single(cut, inside)
    ]
    if dropped:  # bound only where there is one, so earlier keys stand
        key_inputs["dropped"] = dropped
    if pass_ > 1:  # the first pass's key stays as it was before passes
        key_inputs["pass"] = pass_
    if lines:  # a brief for lines and one for the file never share a key
        key_inputs["lines"] = True
    spec = json.dumps(
        {"reference": rendered, "owners": _run_lengths(owners)}, ensure_ascii=False
    )
    return Question(
        key=question_key(Stage.GROUPS, template, key_inputs, KIND),
        kind=KIND,
        stage=Stage.GROUPS,
        template=template,
        prompt=LINES_KICKOFF if lines else KICKOFF,
        handles={
            **{handle: block for block, handle in handles.items()},
            **{handle: block for block, handle in context.items()},
            **{f"g{n}": group.id for n, group in enumerate(ordered, 1)},
        },
        input_cut=cut,
        workspace=front_lines.workspace(Stage.GROUPS, rendered, template.render())
        if lines
        else Workspace(
            files={GROUPS_FILE: rendered},
            edit=GROUPS_FILE,
            instructions=template.render(),
            reask_from_edit=True,
            gate=Gate(module=__name__, spec=spec),
        ),
    )


def group_lines(
    document: Document, cut: Cut, chunk: Chunk
) -> tuple[list[Group], dict[str, list[str]], dict[str, list[str]]]:
    """The cut's groups that lie in the chunk, in file order (of two
    starting at one block, the longer first), and their lines as `render`
    takes them: by block, the start lines to put just before its first
    marker and the end lines just after its text. A group of one block,
    which an older cut may hold, is not shown, and every answer drops it
    (`_single`)."""
    inside = set(chunk.blocks)
    runs = shown_runs(cut, [*chunk.before, *chunk.blocks, *chunk.after])
    first_run: dict[str, int] = {}
    last_run: dict[str, int] = {}
    for n, run in enumerate(runs):
        first_run.setdefault(run.block, n)
        last_run[run.block] = n
    ordered = sorted(
        (g for g in cut.groups if set(g.blocks) <= inside and len(g.blocks) > 1),
        key=lambda g: (first_run[g.blocks[0]], -len(g.blocks)),
    )
    before: dict[str, list[str]] = {}
    after: dict[str, list[str]] = {}
    names = _names(document, cut, ordered)
    for group, name in zip(ordered, names, strict=True):
        before.setdefault(group.blocks[0], []).append(start_line(group.kind, name))
    # The later a group starts, the sooner it ends.
    for group, name in reversed(list(zip(ordered, names, strict=True))):
        last = max(group.blocks, key=lambda block: last_run[block])
        after.setdefault(last, []).append(end_line(name))
    return ordered, before, after


def _names(document: Document, cut: Cut, groups: Sequence[Group]) -> list[str]:
    """Each group's name: its first block's first words in snake case,
    LaTeX commands left out; as many more words as tell it from the groups
    before it, and a number after where none do."""
    found: list[str] = []
    for group in groups:
        text = " ".join(owned_runs(document, cut.block(group.blocks[0])))
        words = [w.lower() for w in _WORD.findall(_COMMAND.sub(" ", text))]
        taken = {_key(name) for name in found}
        count = NAME_WORDS
        name = "_".join(words[:count]) or group.kind.value.replace("-", "_")
        while _key(name) in taken and count < len(words):
            count += 1
            name = "_".join(words[:count])
        base, n = name, 2
        while _key(name) in taken:
            name, n = f"{base}_{n}", n + 1
        found.append(name)
    return found


def _single(cut: Cut, inside: set[str]) -> list[Group]:
    """The cut's one-block groups in the chunk: an older cut's, since a
    group holds two or more blocks.
    The file does not show them, and every answer ungroups them, so a
    session's group from the same block is not refused for a group it
    cannot see."""
    return [g for g in cut.groups if len(g.blocks) == 1 and g.blocks[0] in inside]


def _run_lengths(owners: Sequence[str]) -> list[tuple[str, int]]:
    """`owners` as runs of one handle: the gate's spec, kept small."""
    return [(handle, len(list(run))) for handle, run in groupby(owners)]


# --- reading the file ---------------------------------------------------------------


def _tail(text: str, limit: int = 50) -> str:
    text = " ".join(text.split())
    return f"“{text}”" if len(text) <= limit else f"“…{text[-limit:]}”"


@dataclass(frozen=True, slots=True)
class _Line:
    """A start line (with its kind) or an end line (`kind` None)."""

    kind: GroupKind | None
    name: str
    at: int
    line: int
    text: str

    @property
    def end(self) -> bool:
        return self.kind is None

    @property
    def key(self) -> str:
        return _key(self.name)


def _fixed(tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    return None if _is_group(tokens) or is_flag(tokens) else tokens


def _flag_lines(
    reference: str, edited: str, owners: Sequence[str]
) -> tuple[list[FlagLine], list[str]]:
    return flag_lines(reference, edited, owners, STAGE_SIGNALS[Stage.GROUPS.value])


def read_groups(
    reference: str, edited: str, owners: Sequence[str]
) -> tuple[GroupSpan, ...]:
    """The groups `edited` marks, in the order their first lines stand.
    `reference` is the file as shown and `owners` the handle of the block
    each of its non-whitespace characters of text belongs to (`b…` in the
    chunk, `c…` in its context). Raises `ParseError` with every problem
    found, each with where it is and what to do."""
    changed = text_kept(reference, edited)
    if changed:
        raise ParseError("\n".join(changed))
    problems = fixed_lines(
        reference,
        edited,
        _fixed,
        "the blocks' lines and their roles",
        f"is not a group line, and the other lines are fixed: write {_FORM}, "
        "or a flag line under a block, or remove it",
    )
    problems += _flag_lines(reference, edited, owners)[1]
    found = read_markers(edited)
    lines = edited.splitlines()
    kept = [
        m for m in found.markers if not _is_group(m.tokens) and not is_flag(m.tokens)
    ]

    first_at: dict[str, int] = {}
    last_at: dict[str, int] = {}
    for n, handle in enumerate(owners):
        first_at.setdefault(handle, n)
        last_at[handle] = n
    # Where a group may start: a chunk block's first character, unless the
    # block lies inside another's text (a footnote in its paragraph).
    # a block lies inside another's text when one that starts before it
    # ends after it: the furthest end among the blocks started before it
    starts = []
    furthest = -1
    for handle in sorted(first_at, key=first_at.__getitem__):
        if _in_chunk(handle) and furthest <= last_at[handle]:
            starts.append(first_at[handle])
        furthest = max(furthest, last_at[handle])
    marker_of = {
        m.tokens[0]: m
        for m in kept
        if m.tokens and m.tokens[0] in first_at and CONTINUED not in m.tokens[1:]
    }

    def before_marker(at: int) -> str:
        """The first marker line where the text of one block meets
        another's, for a suggestion."""
        marker = next((m for m in kept if m.at == at), None)
        return (
            f"{marker.text} (line {marker.line})" if marker else "the end of the file"
        )

    def context(line: int) -> str:
        """The text just above a line, if the line above it is text."""
        for above in range(line - 2, -1, -1):
            text = lines[above].strip()
            if not text:
                continue
            if MARKER.match(text) or HEADER.match(text):
                return ""
            return f", after {_tail(text)}"
        return ""

    read: list[_Line] = []
    named: dict[str, int] = {}  # a start's name, and its line
    for marker in found.markers:
        if not _is_group(marker.tokens):
            continue
        where = f"line {marker.line}: {marker.text}"
        words = " ".join(marker.tokens)
        kind: GroupKind | None = None
        if (ends := _END.match(words)) is not None:
            name = ends.group(1)
        elif (starts_ := _START.match(words)) is not None:
            kind_word, name = starts_.group(1), starts_.group(2)
            kind = next((k for k in KINDS if k.value == kind_word), None)
            if kind is None:
                kinds = ", ".join(k.value for k in KINDS)
                problems.append(f"{where}: {kind_word!r} is not a kind: {kinds}")
                continue
            other = named.setdefault(_key(name), marker.line)
            if other != marker.line:
                problems.append(
                    f"lines {other} and {marker.line}: two groups are named "
                    f"{name}; give each its own name"
                )
                continue
        else:
            problems.append(f"{where} is not a group line: write {_FORM}")
            continue
        end = kind is None
        at = marker.at
        # The block whose text the line stands before (a start) or after
        # (an end).
        near = owners[at] if not end and at < len(owners) else None
        if end and at > 0:
            near = owners[at - 1]
        if near is not None and not _in_chunk(near):
            problems.append(
                f"{where} lies in the ⟦{CONTEXT}⟧, shown for reading: a group "
                "holds blocks of this part only"
            )
            continue
        if not end and (at >= len(owners) or first_at[owners[at]] != at):
            if at >= len(owners):
                problems.append(
                    f"{where} is at the end of the file: a group starts on its "
                    "own line just before its first block's marker"
                )
            else:
                here = owners[at]
                later = [s for s in starts if s > at]
                suggested = [
                    f"{marker_of[owners[s]].text} (line {marker_of[owners[s]].line})"
                    for s in [first_at[here], *later[:1]]
                    if owners[s] in marker_of
                ]
                problems.append(
                    f"{where} is inside {here}'s text{context(marker.line)}: a "
                    "group starts on its own line just before its first block's "
                    "marker, such as " + " or ".join(suggested)
                )
            continue
        if end and not (at > 0 and (at == len(owners) or owners[at] != owners[at - 1])):
            if at == 0:
                problems.append(f"{where} is before the first block")
            else:
                here = owners[at]
                suggested = [
                    f"before {before_marker(b)}"
                    for b in [first_at[here], last_at[here] + 1]
                    if b > 0
                ]
                problems.append(
                    f"{where} is inside {here}'s text{context(marker.line)}: a "
                    "group ends on its own line just after its last block's "
                    "text, such as " + " or ".join(suggested)
                )
            continue
        read.append(_Line(kind, name, at, marker.line, marker.text))

    spans: list[GroupSpan] = []
    open_: list[_Line] = []
    for line in read:
        if not line.end:
            open_.append(line)
            continue
        same = [n for n, o in enumerate(open_) if o.key == line.key]
        if not same:
            here = ", ".join(f"{o.text} (line {o.line})" for o in open_)
            problems.append(
                f"line {line.line}: {line.text} ends no group: "
                + (f"open here: {here}" if here else "no group is open here")
            )
            continue
        start = open_.pop(same[-1])
        for crossing in open_[same[-1] :]:
            problems.append(
                f"line {crossing.line}: {crossing.text} starts inside "
                f"{start.text} (lines {start.line} to {line.line}) and ends after "
                "it: nest one inside the other, or make them disjoint"
            )
        span = _span(start, line, owners, first_at, last_at, problems)
        if span is not None:
            spans.append(span)
    for start in open_:
        problems.append(
            f"line {start.line}: {start.text} has no "
            f"{end_line(start.name)}: add it on its "
            "own line just after its last block's text"
        )

    seen: dict[tuple[GroupKind, str], GroupSpan] = {}
    for span in sorted(spans, key=lambda s: s.line):
        other = seen.setdefault((span.kind, span.first), span)
        if other is not span:
            problems.append(
                f"lines {other.line} and {span.line}: two {span.kind.value} "
                f"groups start at {span.first}; keep one"
            )
    if problems:
        raise ParseError("\n".join(problems))
    return tuple(sorted(spans, key=lambda s: s.line))


def _span(
    start: _Line,
    end: _Line,
    owners: Sequence[str],
    first_at: dict[str, int],
    last_at: dict[str, int],
    problems: list[str],
) -> GroupSpan | None:
    where = f"lines {start.line} to {end.line}: {start.text}"
    inside = sorted(set(owners[start.at : end.at]), key=_order)
    if not inside:
        problems.append(f"{where} holds no block")
        return None
    if not all(_in_chunk(h) for h in inside):
        problems.append(
            f"{where} holds text of the ⟦{CONTEXT}⟧, shown for reading: a group "
            "holds blocks of this part only"
        )
        return None
    partly = [h for h in inside if first_at[h] < start.at or last_at[h] >= end.at]
    if partly:
        problems.append(
            f"{where} holds only part of {', '.join(partly)}: a group holds whole "
            "blocks, with every 'continued' part of each"
        )
        return None
    if len(inside) < 2:
        problems.append(
            f"{where} holds one block, {inside[0]}: a group holds two or more; "
            "one block is a node of its own, so remove the group"
        )
        return None
    return GroupSpan(
        start.kind, inside[0], inside[-1], start.at, end.at, start.line, end.line
    )


# --- the answer ---------------------------------------------------------------------


def parse(question: Question, edited: str, document: Document) -> list[CutCorrection]:
    """The corrections the edit gives: the `ungroup`s, then the
    `group`s."""
    assert question.workspace is not None and question.input_cut is not None
    reference = front_lines.shown_file(question)
    handles = {
        block: handle for handle, block in question.handles.items() if handle[0] in "bc"
    }
    owners = _owners(document, question.input_cut, handles)
    shown = read_groups(reference, reference, owners)
    found = list(read_groups(reference, edited, owners))
    left = [(question.handles[f"g{n}"], s) for n, s in enumerate(shown, 1)]
    for group, span in list(left):
        same = next((f for f in found if f.place == span.place), None)
        if same is not None:
            left.remove((group, span))
            found.remove(same)

    def correction(op: CutOp, **fields: Any) -> CutCorrection:
        return CutCorrection(
            id="", op=op, cls=CorrectionClass.OTHER, why=SESSION_WHY, **fields
        )

    inside = {b for h, b in question.handles.items() if h[0] == "b"}
    single = _single(question.input_cut, inside)
    return [
        *(
            replace(correction(CutOp.UNGROUP, group=g.id), why=SINGLE_WHY)
            for g in single
        ),
        *(correction(CutOp.UNGROUP, group=group) for group, _ in left),
        *(
            correction(
                CutOp.GROUP,
                kind=span.kind,
                first=question.handles[span.first],
                last=question.handles[span.last],
                names=NAMES_NONE,
            )
            for span in found
        ),
    ]


def check(question: Question, text: str, document: Document) -> Validation:
    """An edited `groups.md`, or the lines laid into it (`front_lines`),
    parsed, and its corrections applied alone to the question's cut, with
    its flags."""
    return front_lines.checked(
        question, text, lambda f: _checked(question, f, document)
    )


def _checked(question: Question, text: str, document: Document) -> Validation:
    try:
        corrections = parse(question, text, document)
    except ParseError as error:
        return Validation(problems=tuple(str(error).split("\n")))
    checked = applied_alone(question, corrections, document)
    if not checked.valid:
        return checked
    assert question.workspace is not None and question.input_cut is not None
    handles = {
        block: handle for handle, block in question.handles.items() if handle[0] in "bc"
    }
    owners = _owners(document, question.input_cut, handles)
    lines, _ = _flag_lines(front_lines.shown_file(question), text, owners)
    return replace(checked, flags=as_flags(lines, question.handles))


# --- the gate -----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """The session's gate, as Claude Code's Stop hook: nothing when the
    edited file reads, else Claude Code's `block` decision with its
    problems, at most `gates.ROUNDS` times. It never fails the session: a
    gate that cannot run lets it end, and the runner's check decides."""
    gates.hook_input()
    try:
        spec_path, edited_path = map(Path, argv if argv is not None else sys.argv[1:3])
        spec = gates.spec(spec_path)
        owners = [handle for handle, count in spec["owners"] for _ in range(count)]
        read_groups(spec["reference"], gates.read_regular(edited_path), owners)
    except ParseError as error:
        problems = str(error).split("\n")
    except Exception as error:  # noqa: BLE001 - the gate must never trap a session
        print(f"the groups gate could not run: {error}", file=sys.stderr)
        return 0
    else:
        return 0
    if gates.round_left(spec_path):
        gates.block(gates.reason(edited_path.name, problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
