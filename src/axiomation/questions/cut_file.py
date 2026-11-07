"""The cut review as a session on one file, per chunk.

A chunk's blocks are written out as `cut.md`: every block's text in
reading order under a marker line, `⟦b3 claim⟧ — form: …`. The author
corrects the cut by editing the markers only: a new `⟦new⟧` line splits a
block, a deleted marker joins a block to the one before it, a moved one
moves the boundary. Only cuts are read back: a changed role word changes
nothing, since the roles session owns roles. Blocks under
`⟦context⟧` are the chunk's neighbours, shown for
reading. Beside it, unless left out, `outline.md`: the paper's headings
and labelled statements, and where the chunk lies.

A build's author pass reads the file as `paper.md` and answers as lines
(`front_lines`), one a correction: `split b12 "<words>"`, `join b13`,
`move b13 "<words>"`. Its brief is the author's up to its part on the
file it edits, then `LINES_TEMPLATE`'s section; the lines are laid into
the file as the markers an edit would have moved. A person's edit is of
the file itself.

The judge's session is the same file, rendered from the cut the
author left: a marker the author added is flagged `proposed`, a boundary
the author removed is shown again flagged `joined`, and the judge
declines an edit by flagging its marker `declined` with a short reason.
It only keeps or declines: its answer's corrections are the reverts of
the edits it declined, and any other change fails it. The ledger of those
declines, settled, is written into the next author's files as
`⟦declined split⟧ — <reason>` and `⟦declined join⟧ — <reason>` lines,
which `read_markers` ignores.

The answer is the edited `cut.md`, or the lines laid into it. It is parsed
back into the review's corrections by comparing it with the file as
rendered, character for character outside the markers, and validated as
every cut-review answer is (`cut_review.validate`): an answer is used
whole or not at all.

The key is the kind, the stage, the template, the chunk's blocks as the
file shows them, the pass or the author key, and the flags and ledger the
file carries; the context blocks and the outline are context.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from axiomation.assembler import owned_runs
from axiomation.cutter.model import Block, Cut, Form
from axiomation.questions import (
    Question,
    Stage,
    Template,
    Validation,
    Workspace,
    load_template,
)
from axiomation.questions.chunks import (
    DEFAULT_OVERLAP,
    Chunk,
    chunks,
    sections_held,
)
from axiomation.questions.cut_review import (
    KIND,
    labels_by_block,
    question_key,
    role_key,
    validate,
)
from axiomation.reader import Document
from axiomation.reader.document import Span
from axiomation.records import Decline, Flag, Signal

FILE_TEMPLATE = "cut-review-file"
JUDGE_TEMPLATE = "cut-review-judge"
CUT_FILE = "cut.md"
OUTLINE_FILE = "outline.md"
KICKOFF = "Correct the cut in cut.md, as your instructions say."
#: An author pass answered as lines: its section, which takes the place of
#: the author's brief from `FILE_PART` on, and its kickoff.
LINES_TEMPLATE = "cut-review-lines"
FILE_PART = "\n## The file you edit"
LINES_KICKOFF = (
    "Write your corrections to the cut of paper.md in cuts.md, as your "
    "instructions say."
)
JUDGE_KICKOFF = "Judge the proposed edits in cut.md, as your instructions say."

CONTEXT = "context"
CONTINUED = "continued"

#: The flags a marker line may carry. `proposed`: the author
#: added this boundary. `joined`: the author removed it. `declined`: the
#: judge declined the edit here.
PROPOSED = "proposed"
JOINED = "joined"
DECLINED = "declined"
FLAGS = frozenset({PROPOSED, JOINED, DECLINED})

#: What a ledger line says was declined: `⟦declined split⟧` (no boundary
#: there) or `⟦declined join⟧` (the boundary stays). Shown to later
#: authors as settled, and never read back.
SPLIT = "split"
JOIN = "join"

MARKER = re.compile(r"^\s*⟦([^⟧]*)⟧(.*)$")
HEADER = re.compile(r"^##### (\S.*)$")

#: An outline entry shows this many words of a statement.
OUTLINE_WORDS = 12

#: A split's excerpt: the fewest words from the cut point that occur once
#: in the block, from this many up.
EXCERPT_WORDS = 3


class RenderError(ValueError):
    """A paper whose text would read as a marker or a file line."""


# --- the questions ------------------------------------------------------------------


#: The brief's sentences on the outline, left empty when a session has none.
OUTLINE_BRIEF = {
    "outline_intro": " `outline.md` shows where that part lies in the whole.",
    "outline_note": "`outline.md` lists the paper's headings and its labelled "
    "statements with\ntheir first words, and marks where your part lies. ",
}


#: The brief's paragraph on the ledger, left empty when a pass has none.
LEDGER_NOTE = (
    "A line `⟦declined split⟧ — …` or `⟦declined join⟧ — …` is an edit a\n"
    "previous session made and a judge declined, with its reason. A declined\n"
    "split is not a boundary; a declined join is one. The decision is settled:\n"
    "leave that place as it is.\n\n"
)


def file_questions(
    document: Document,
    cut: Cut,
    budget: int | None,
    template: Template,
    overlap: int = DEFAULT_OVERLAP,
    pass_: int = 1,
    with_outline: bool = True,
    ledger: Sequence[Insertion] = (),
    lines: bool = False,
) -> tuple[Question, ...]:
    """One session per chunk, in reading order. `pass_` is part of the
    key: a later pass asks a chunk again even where the one before left
    it unchanged, and so is whether the session has the outline: without
    it, a session gets `cut.md` alone and a brief that does not name it.
    `ledger` is the judge's rejections to show, where they apply; it is part of the key
    when there is one. With `lines`, a session reads the file as `paper.md`
    and answers as lines (`front_lines`), under the brief up to its part on
    the file, then `LINES_TEMPLATE`'s section, whose version is in the
    key."""
    values = {
        **{name: text if with_outline else "" for name, text in OUTLINE_BRIEF.items()},
        "ledger_note": LEDGER_NOTE if ledger else "",
    }
    instructions = template.render(**values)
    key_extra: dict[str, Any] = {"pass": pass_, "outline": with_outline}
    prompt = KICKOFF
    if lines:
        head, found, _ = instructions.partition(FILE_PART)
        if not found:
            raise RenderError(
                f"the cut's brief {template.name!r} has no "
                f"{FILE_PART.strip()!r} part for the lines' section to take "
                "the place of"
            )
        section = load_template(LINES_TEMPLATE)
        instructions = head.rstrip("\n") + "\n\n" + section.render(**values)
        key_extra["lines_brief"] = section.version
        prompt = LINES_KICKOFF
    return chunk_questions(
        document,
        cut,
        chunks(document, cut, budget, overlap),
        template,
        instructions,
        prompt,
        Stage.BOUNDARIES,
        key_extra,
        insertions=ledger,
        with_outline=with_outline,
        lines=lines,
    )


def judge_questions(
    document: Document,
    cut: Cut,
    before: Cut,
    author_keys: Sequence[str],
    budget: int | None,
    template: Template,
    overlap: int = DEFAULT_OVERLAP,
    with_outline: bool = True,
) -> tuple[Question, ...]:
    """The judge's sessions: one per chunk the author worked on
    (`before`'s chunks, with the blocks `cut` left in them, so a split's
    pieces stay together), with the author's edits flagged against
    `before`. `author_keys` names the author questions, one per author
    chunk; the judge's question for a chunk names the author question it
    reviewed, and that key is part of its own."""
    instructions = template.render(
        **{name: text if with_outline else "" for name, text in OUTLINE_BRIEF.items()}
    )
    found: list[Chunk] = []
    judges: list[str | None] = []
    for index, chunk in enumerate(chunks(document, before, budget, overlap)):
        blocks = _within(cut, before, chunk.blocks)
        if not blocks:
            continue
        found.append(Chunk(blocks))
        judges.append(author_keys[index] if index < len(author_keys) else None)
    return chunk_questions(
        document,
        cut,
        found,
        template,
        instructions,
        JUDGE_KICKOFF,
        Stage.JUDGE,
        {"outline": with_outline},
        before=before,
        judges_of=judges,
        with_outline=with_outline,
    )


def _within(cut: Cut, before: Cut, blocks: Sequence[str]) -> tuple[str, ...]:
    """The blocks `cut` left in the region `blocks` covers in `before`."""
    ranges = _ranges(before, blocks)
    return tuple(
        block.id
        for block in cut.blocks
        if any(
            block.extent.file == file and start <= block.extent.start < end
            for file, start, end in ranges
        )
    )


def _ranges(cut: Cut, blocks: Sequence[str]) -> list[tuple[str, int, int]]:
    """The (file, start, end) the blocks cover, merged where they meet."""
    spans = sorted(
        (cut.block(b).extent.file, cut.block(b).extent.start, cut.block(b).extent.end)
        for b in blocks
    )
    merged: list[tuple[str, int, int]] = []
    for file, start, end in spans:
        if merged and merged[-1][0] == file and start <= merged[-1][2]:
            merged[-1] = (file, merged[-1][1], max(merged[-1][2], end))
        else:
            merged.append((file, start, end))
    return merged


def chunk_questions(
    document: Document,
    cut: Cut,
    found_chunks: Sequence[Chunk],
    template: Template,
    instructions: str,
    prompt: str,
    stage: Stage,
    key_extra: dict[str, Any],
    before: Cut | None = None,
    insertions: Sequence[Insertion] = (),
    judges_of: Sequence[str | None] = (),
    with_outline: bool = True,
    before_lines: Mapping[str, Sequence[str]] | None = None,
    after_lines: Mapping[str, Sequence[str]] | None = None,
    lines: bool = False,
) -> tuple[Question, ...]:
    """The chunk sessions the author, the judge and the deciding step
    share: the chunk's blocks, its `cut.md`, its outline, and a question
    per chunk. `judges_of` names, per chunk, the author question a judge's
    session reviews; `before_lines` and `after_lines` are lines
    `render` puts around blocks (the deciding step's group lines),
    bound in the key where there are any. With `lines`, the session reads
    `cut.md` as `paper.md` and answers as lines (`front_lines`)."""
    # front_lines reads this module's file, so it imports this module
    from axiomation.questions import front_lines

    labels = labels_by_block(document, cut)
    questions = []
    for n, chunk in enumerate(found_chunks):
        judges = judges_of[n] if n < len(judges_of) else None
        handles = {block: f"b{n}" for n, block in enumerate(chunk.blocks, 1)}
        proposed, marks = (
            edit_marks(document, before, cut, handles)
            if before is not None
            else (frozenset(), [])
        )
        marks = [*marks, *insertions]
        inputs = [
            block_inputs(document, cut, cut.block(b), handles[b], handles, labels)
            for b in chunk.blocks
        ]
        rendered = render(
            document,
            cut,
            chunk,
            handles,
            labels,
            proposed,
            marks,
            before=before_lines,
            after=after_lines,
        )
        beside: dict[str, str] = {}
        if with_outline:
            shown = front_lines.PAPER_FILE if lines else CUT_FILE
            beside[OUTLINE_FILE] = outline(document, cut, chunk, labels, shown)
        nested = [handles[b] for b in chunk.blocks if cut.block(b).within is not None]
        workspace = (
            front_lines.workspace(
                Stage.BOUNDARIES, rendered, instructions, beside, nested
            )
            if lines
            else Workspace(
                files={CUT_FILE: rendered, **beside},
                edit=CUT_FILE,
                instructions=instructions,
            )
        )
        extra = dict(key_extra)
        if judges is not None:
            extra["author"] = judges
        if proposed:
            extra["proposed"] = sorted(b for b in proposed if b in handles)
        if marks:
            extra["marks"] = [
                {"file": m.file, "offset": m.offset, "line": m.line} for m in marks
            ]
        around = {
            side: {
                handles[b]: list(lines) for b, lines in given.items() if b in handles
            }
            for side, given in (("before", before_lines), ("after", after_lines))
            if given
        }
        if any(around.values()):
            extra["lines"] = around
        questions.append(
            Question(
                key=question_key(stage, template, {"chunk": inputs, **extra}),
                kind=KIND,
                stage=stage,
                template=template,
                prompt=prompt,
                handles={
                    **{handle: block for block, handle in handles.items()},
                    **{
                        f"c{n}": block
                        for n, block in enumerate([*chunk.before, *chunk.after], 1)
                    },
                },
                input_cut=cut,
                workspace=workspace,
                judges=judges,
            )
        )
    return tuple(questions)


# --- what the file shows of a block -------------------------------------------------


def _block_text(
    document: Document, cut: Cut, block: Block, handles: dict[str, str]
) -> list[str]:
    """A block's owned runs in order; between two runs a gap line, `⟦b4⟧`
    for each block nested there, else `[…]`."""
    nested = [b for b in cut.blocks if b.within == block.id]
    items: list[tuple[int, int, str]] = [
        (run.start, 0, document.text(run)) for run in block.owned
    ]
    items += [(b.extent.start, 1, f"⟦{handles.get(b.id, '…')}⟧") for b in nested]
    items.sort(key=lambda item: (item[0], item[1]))
    lines: list[str] = []
    previous_was_run = False
    for _, kind, text in items:
        if kind == 0 and previous_was_run:
            lines.append("[…]")
        lines.append(text)
        previous_was_run = kind == 0
    return lines


def block_inputs(
    document: Document,
    cut: Cut,
    block: Block,
    handle: str,
    handles: dict[str, str],
    labels: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    """Everything the file shows of one block, with its id: the key's
    inputs and what its marker line renders."""
    return {
        "handle": handle,
        "id": block.id,
        "form": block.form.value,
        "environment": block.environment,
        "meaning": block.meaning,
        "role": role_key(block.roles),
        "labels": list(labels.get(block.id, ())),
        "doubts": [doubt.value for doubt in block.doubts],
        "parts": [
            {"kind": part.kind.value, "marker": part.marker}
            for part in cut.parts
            if part.block == block.id
        ],
        "within": handles.get(block.within) if block.within else None,
        "text": _block_text(document, cut, block, handles),
    }


def _facts(inputs: dict[str, Any]) -> str:
    environment = inputs["environment"] or "none"
    if inputs["meaning"] and inputs["meaning"] != inputs["environment"]:
        environment += f" (a {inputs['meaning']})"
    parts = "; ".join(
        f"{part['kind']} {json.dumps(part['marker'], ensure_ascii=False)}"
        for part in inputs["parts"]
    )
    return (
        f"form: {inputs['form']}; environment: {environment}; "
        f"labels: {', '.join(inputs['labels']) or 'none'}; "
        f"the cutter's doubts: {', '.join(inputs['doubts']) or 'none'}; "
        f"marked parts: {parts or 'none'}"
    )


@dataclass(frozen=True, slots=True)
class ShownRun:
    """One owned run of a shown block; `first` for a block's first run."""

    span: Span
    block: str
    first: bool


@dataclass(frozen=True, slots=True)
class Insertion:
    """A marker line the render adds at an offset in a file's text: a
    `joined` marker for an author's merge, or a ledger line."""

    file: str
    offset: int
    line: str


def edit_marks(
    document: Document, before: Cut, cut: Cut, handles: dict[str, str]
) -> tuple[frozenset[str], list[Insertion]]:
    """What an author's pass changed, for the judge's file: the
    blocks `cut` added since `before` (a split's pieces, to flag
    `proposed`), and a `joined` marker at each boundary `before` had and
    `cut` has not (a merge), naming the block it joined into."""
    before_ids = {b.id for b in before.blocks}
    cut_ids = {b.id for b in cut.blocks}
    proposed = frozenset(b.id for b in cut.blocks if b.id not in before_ids)
    insertions: list[Insertion] = []
    for gone in before.blocks:
        if gone.id in cut_ids:
            continue
        host = next(
            (
                b
                for b in cut.blocks
                if b.extent.file == gone.extent.file
                and b.extent.start <= gone.extent.start < b.extent.end
            ),
            None,
        )
        if host is None or host.id not in handles:
            continue
        insertions.append(
            Insertion(
                gone.extent.file,
                gone.extent.start,
                f"⟦{JOINED}⟧ — {handles[host.id]} took in "
                f"{_first_words(document, gone)} …",
            )
        )
    return proposed, insertions


def settled(what: str, reason: str) -> str:
    """The ledger's line for one declined edit: `what` is
    `SPLIT` or `JOIN`."""
    return f"⟦{DECLINED} {what}⟧ — {reason}"


def _ledger_line(tokens: tuple[str, ...]) -> bool:
    return len(tokens) > 1 and tokens[0] == DECLINED and tokens[1] in (SPLIT, JOIN)


def _slot(segments: list[ShownRun], file: str, offset: int) -> tuple[int, int] | None:
    """The segment a line at `offset` goes in, and the index in its text:
    the run holding the offset, else the run before it, else the first of
    the file. `None` when the offset lies outside this chunk's runs
    (another chunk holds it)."""
    found = [(n, s) for n, s in enumerate(segments) if s.span.file == file]
    if not found:
        return None
    if not (found[0][1].span.start <= offset < found[-1][1].span.end):
        return None
    for n, s in found:
        if s.span.start <= offset < s.span.end:
            return n, offset - s.span.start
    before = [(n, s) for n, s in found if s.span.start <= offset]
    if before:
        n, s = before[-1]
        return n, s.span.end - s.span.start
    return found[0][0], 0


def shown_runs(cut: Cut, shown: list[str]) -> list[ShownRun]:
    """The shown blocks' runs in reading order: by the unit each lies in
    (a block with those nested in it), then by offset."""
    rank = {block: n for n, block in enumerate(shown)}

    def outermost(block_id: str) -> str:
        within = cut.block(block_id).within
        while within is not None and within in rank:
            block_id, within = within, cut.block(within).within
        return block_id

    found = [
        ShownRun(run, block_id, n == 0)
        for block_id in shown
        for n, run in enumerate(cut.block(block_id).owned)
    ]
    found.sort(key=lambda s: (rank[outermost(s.block)], s.span.start))
    return found


def render(
    document: Document,
    cut: Cut,
    chunk: Chunk,
    handles: dict[str, str],
    labels: dict[str, tuple[str, ...]],
    proposed: Collection[str] = (),
    insertions: Sequence[Insertion] = (),
    label: Callable[[Block], str] | None = None,
    before: Mapping[str, Sequence[str]] | None = None,
    after: Mapping[str, Sequence[str]] | None = None,
    context: Callable[[Block], str] | None = None,
) -> str:
    """The chunk as `cut.md`: its context before, its blocks, its context
    after, a `##### <file>` line wherever the file changes. A block in
    `proposed` (an author's split) is flagged, and each `Insertion` is a
    marker line placed at its offset. With `label`, a marker
    carries the handle and the label's word for the block, if any, and no
    description (the roles session). `before` gives, by block,
    lines to put just before its first marker, and `after` lines to put
    just after the last of its runs the file shows (the groups session).
    With `context`, each context block has a marker of its own, `context`'s
    description of it after the marker (the touched roles
    session); else one marker stands for a run of them."""
    shown = [*chunk.before, *chunk.blocks, *chunk.after]
    segments = shown_runs(cut, shown)
    last_run = {segment.block: n for n, segment in enumerate(segments)}
    slots: dict[int, list[tuple[int, str]]] = {}
    for insertion in insertions:
        slot = _slot(segments, insertion.file, insertion.offset)
        if slot is not None:
            n, at = slot
            slots.setdefault(n, []).append((at, insertion.line))
    lines: list[str] = []
    current, previous = None, None
    for n, segment in enumerate(segments):
        if segment.span.file != current:
            lines += ["", f"##### {segment.span.file}"]
            current, previous = segment.span.file, None
        block = cut.block(segment.block)
        if segment.block not in handles:
            if context is not None:
                if segment.block != previous:
                    said = context(block) if segment.first else ""
                    lines += ["", f"⟦{CONTEXT}⟧" + (f" — {said}" if said else "")]
            # One marker for a run of context, however many blocks.
            elif previous is None or previous in handles:
                lines += ["", f"⟦{CONTEXT}⟧"]
        elif segment.first:
            handle = handles[segment.block]
            for line in (before or {}).get(segment.block, ()):
                lines += ["", line]
            inputs = block_inputs(document, cut, block, handle, handles, labels)
            if label is not None:
                word = label(block)
                lines += ["", f"⟦{handle} {word}⟧" if word else f"⟦{handle}⟧"]
            else:
                role = ", ".join(r.value for r in block.roles) or "none"
                flag = f" {PROPOSED}" if segment.block in proposed else ""
                lines += ["", f"⟦{handle} {role}{flag}⟧ — {_facts(inputs)}"]
        elif previous != segment.block:
            lines += ["", f"⟦{handles[segment.block]} {CONTINUED}⟧"]
        text = document.text(segment.span)
        for line in text.splitlines():
            if MARKER.match(line) or HEADER.match(line):
                raise RenderError(
                    f"a line of {segment.block} would read as a marker or a "
                    f"file line in {CUT_FILE}: {line.strip()[:80]!r}"
                )
        for at, line in sorted(slots.get(n, []), reverse=True):
            text = f"{text[:at]}\n{line}\n{text[at:]}"
        lines.append(text)
        if last_run[segment.block] == n:
            for line in (after or {}).get(segment.block, ()):
                lines += ["", line]
        previous = segment.block
    return "\n".join(lines).lstrip("\n") + "\n"


def outline(
    document: Document,
    cut: Cut,
    chunk: Chunk,
    labels: dict[str, tuple[str, ...]],
    shown: str = CUT_FILE,
) -> str:
    """The paper's headings, indented by level, and its labelled
    statements with their first words; two lines mark where the chunk,
    shown to the session as `shown`, starts and ends."""
    held = sections_held(document, cut)
    levels = sorted({section.level for section in held.values()})
    inside = set(chunk.blocks)
    lines = [
        "# Outline of the paper",
        "",
        "The paper's headings, and its labelled statements with their first "
        f"words. The part in `{shown}` lies between the two `→` lines.",
        "",
    ]
    depth = 0
    started = ended = False
    for block in cut.blocks:
        if block.id in inside and not started:
            lines.append(f"→ `{shown}` starts here: {_first_words(document, block)} …")
            started = True
        elif started and not ended and block.id not in inside:
            lines.append(f"→ `{shown}` ends here")
            ended = True
        section = held.get(block.id) if block.within is None else None
        if section is not None:
            depth = levels.index(section.level)
            lines.append(f"{'  ' * depth}- {section.title}")
            depth += 1
        elif block.form is Form.STATEMENT and labels.get(block.id):
            kind = block.environment or block.form.value
            lines.append(
                f"{'  ' * depth}- {kind} ({', '.join(labels[block.id])}): "
                f"{_first_words(document, block)} …"
            )
    if started and not ended:
        lines.append(f"→ `{shown}` ends here")
    return "\n".join(lines) + "\n"


def _first_words(document: Document, block: Block) -> str:
    words = [
        word
        for word in " ".join(owned_runs(document, block)).split()
        if not word.startswith(("\\begin{", "\\end{"))
    ]
    return " ".join(words[:OUTLINE_WORDS])


# --- the answer ---------------------------------------------------------------------


class ParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _Marker:
    at: int  # the non-whitespace characters before it
    tokens: tuple[str, ...]
    line: int
    text: str


@dataclass(frozen=True, slots=True)
class _Read:
    headers: tuple[tuple[str, int], ...]
    markers: tuple[_Marker, ...]
    stream: str


def read_markers(text: str) -> _Read:
    """A file's `##### <file>` lines and markers, each at the count of
    non-whitespace characters before it, and those characters."""
    headers: list[tuple[str, int]] = []
    markers: list[_Marker] = []
    stream: list[str] = []
    at = 0
    for number, line in enumerate(text.splitlines(), 1):
        header = HEADER.match(line)
        if header:
            headers.append((header.group(1).strip(), at))
            continue
        marker = MARKER.match(line)
        if marker:
            tokens = tuple(marker.group(1).split())
            if _ledger_line(tokens):
                continue  # the ledger: shown, never read
            markers.append(_Marker(at, tokens, number, line.strip()))
            continue
        # `split()` drops what `isspace` calls whitespace
        kept = "".join(line.split())
        stream.append(kept)
        at += len(kept)
    return _Read(tuple(headers), tuple(markers), "".join(stream))


# --- what a session may not change -----------------------------------------------


def _stream_lines(text: str) -> list[int]:
    """For each character of `read_markers(text).stream`, its line."""
    found: list[int] = []
    for number, line in enumerate(text.splitlines(), 1):
        if HEADER.match(line) or MARKER.match(line):
            continue
        found.extend([number] * sum(len(word) for word in line.split()))
    return found


def quoted(text: str, limit: int = 60) -> str:
    """A line's words, quoted for a problem, cut at `limit` characters."""
    text = " ".join(text.split())
    return f"“{text}”" if len(text) <= limit else f"“{text[:limit]}…”"


def text_kept(reference: str, edited: str) -> list[str]:
    """Where `edited` does not show `reference`'s text and `##### <file>`
    lines: the problems, the first change of text with its line and both
    versions quoted; none when it does."""
    shown, found = read_markers(reference), read_markers(edited)
    problems: list[str] = []
    if found.stream != shown.stream:
        at = next(
            (
                i
                for i, (a, b) in enumerate(
                    zip(found.stream, shown.stream, strict=False)
                )
                if a != b
            ),
            min(len(found.stream), len(shown.stream)),
        )
        now_lines, then_lines = _stream_lines(edited), _stream_lines(reference)
        line = now_lines[min(at, len(now_lines) - 1)] if now_lines else 1
        was = then_lines[min(at, len(then_lines) - 1)] if then_lines else 1
        now = edited.splitlines()[line - 1] if edited.splitlines() else ""
        then = reference.splitlines()[was - 1] if reference.splitlines() else ""
        problems.append(
            f"line {line}: the paper's text changed: it reads {quoted(now)} "
            f"where it read {quoted(then)}; put the text back as it was"
        )
    # Where a header stands moves with the text: compared only when it held.
    elif found.headers != shown.headers:
        problems.append(
            "a '##### <file>' line was changed, moved or removed; put it back as it was"
        )
    return problems


def fixed_lines(
    reference: str,
    edited: str,
    identity: Callable[[tuple[str, ...]], object | None],
    fixed: str,
    added: str,
) -> list[str]:
    """What changed among a file's fixed marker lines: those `identity`
    gives a value, compared by that value and where they stand; a marker
    it gives `None` is the session's to edit. `fixed` names the fixed
    lines in a problem, `added` says what to do with a line added among
    them."""

    def kept(markers: Sequence[_Marker]) -> list[_Marker]:
        return [m for m in markers if identity(m.tokens) is not None]

    then = kept(read_markers(reference).markers)
    now = kept(read_markers(edited).markers)
    lines = reference.splitlines()

    def before(marker: _Marker) -> str:
        following = next((t for t in lines[marker.line :] if t.strip()), "")
        return quoted(following)

    matcher = difflib.SequenceMatcher(
        a=[(m.at, identity(m.tokens)) for m in then],
        b=[(m.at, identity(m.tokens)) for m in now],
        autojunk=False,
    )
    problems: list[str] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        gone, new = then[i1:i2], now[j1:j2]
        for old, line in zip(gone, new, strict=False):
            if identity(old.tokens) == identity(line.tokens):
                problems.append(
                    f"line {line.line}: {line.text} moved; {fixed} are fixed: put "
                    f"it back just before {before(old)}"
                )
            else:
                problems.append(
                    f"line {line.line}: {line.text} was {old.text}; {fixed} are "
                    "fixed: put it back as it was"
                )
        for old in gone[len(new) :]:
            problems.append(
                f"{old.text} is gone; {fixed} are fixed: put it back just before "
                f"{before(old)}"
            )
        for line in new[len(gone) :]:
            problems.append(f"line {line.line}: {line.text} {added}")
    return problems


#: The head of a flag line: `⟦? <signal> "<evidence>" —
#: <note>⟧`, a position on the cut or an earlier step, under a block.
FLAG_HEAD = "?"
_FLAG_FORM = '⟦? <signal> "<the block\'s words>" — <what you read there>⟧'
_FLAG_LINE = re.compile(
    r'^\?(?:\s+(?P<signal>[^\s"]+))?\s+"(?P<evidence>.*?)"\s+—\s*(?P<note>.*)$'
)


def is_flag(tokens: tuple[str, ...]) -> bool:
    """Whether a marker's tokens are a flag line's."""
    return bool(tokens) and tokens[0].startswith(FLAG_HEAD)


@dataclass(frozen=True, slots=True)
class FlagLine:
    """A flag line read: the handle of the block it stands under, its
    signal word (`None` for none: friction), the block's words it quotes
    and its note."""

    handle: str
    signal: str | None
    evidence: str
    note: str
    line: int


def flag_lines(
    reference: str, edited: str, owners: Sequence[str], signals: Collection[str]
) -> tuple[list[FlagLine], list[str]]:
    """The flag lines of an edited file, and what is wrong with them. A
    flag belongs to the block whose marker stands last above it, one of the
    chunk's (`b…`); its words must stand in that block's text, whitespace
    aside, its signal be one of `signals` or none, and its note not be
    empty. `owners` is the handle of the block each non-whitespace
    character of the file's text belongs to, as the file was shown."""
    stream = read_markers(reference).stream
    texts: dict[str, list[str]] = {}
    for ch, handle in zip(stream, owners, strict=False):
        texts.setdefault(handle, []).append(ch)
    found: list[FlagLine] = []
    problems: list[str] = []
    under: str | None = None
    for marker in read_markers(edited).markers:
        head = marker.tokens[0] if marker.tokens else ""
        if not is_flag(marker.tokens):
            if head in texts or head == CONTEXT:
                under = head if head.startswith("b") else None
            elif head in ("group", "end"):
                under = None  # a group's line: a flag goes under a block's
            continue
        where = f"line {marker.line}: {marker.text}"
        inner = MARKER.match(marker.text)
        line = _FLAG_LINE.match(inner.group(1).strip()) if inner else None
        if line is None:
            problems.append(f"{where} is not a flag line, whose form is {_FLAG_FORM}")
            continue
        if under is None:
            problems.append(
                f"{where} stands under no block of this part: put it on its own "
                "line under the marker of the block it is about"
            )
            continue
        signal = line.group("signal")
        if signal is not None and signal not in signals:
            problems.append(
                f"{where}: {signal!r} is not a signal here: write one of "
                f"{', '.join(signals)}, or no word"
            )
            continue
        evidence, note = line.group("evidence"), line.group("note").strip()
        words = "".join(evidence.split())
        if not words or words not in "".join(texts[under]):
            problems.append(
                f"{where}: the words in quotes are not {under}'s; quote them "
                "from its text"
            )
            continue
        if not note:
            problems.append(f"{where}: say what you read there, after ' — '")
            continue
        found.append(
            FlagLine(under, signal, " ".join(evidence.split()), note, marker.line)
        )
    return found, problems


def as_flags(lines: Sequence[FlagLine], handles: Mapping[str, str]) -> tuple[Flag, ...]:
    """Flag lines as flags, ids left for the resolver; `handles` maps each
    handle to its block's id."""
    return tuple(
        Flag(
            id="",
            answer=None,
            blocks=(handles[line.handle],),
            signal=Signal(line.signal or Signal.OTHER),
            evidence=(line.evidence,),
            note=line.note,
        )
        for line in lines
    )


def places(
    document: Document, cut: Cut, held: Collection[str]
) -> list[tuple[str, int, str]]:
    """For each non-whitespace character a file shows of the blocks
    `held`, in order: its file, its offset there, and the block that owns
    it."""
    shown = [block.id for block in cut.blocks if block.id in held]
    found = []
    for segment in shown_runs(cut, shown):
        span = segment.span
        text = document.file(span.file).text[span.start : span.end]
        found += [
            (span.file, span.start + i, segment.block)
            for i, ch in enumerate(text)
            if not ch.isspace()
        ]
    return found


def places_shown(question: Question, document: Document) -> list[tuple[str, int, str]]:
    """`places` for the blocks a question shows."""
    assert question.input_cut is not None
    return places(document, question.input_cut, set(question.handles.values()))


def check(question: Question, text: str, document: Document) -> Validation:
    """An edited `cut.md`, or the lines laid into it (`front_lines`),
    parsed and validated."""
    # front_lines reads this module's file, so it imports this module
    from axiomation.questions import front_lines

    return front_lines.checked(
        question, text, lambda f: _checked(question, f, document)
    )


def _checked(question: Question, text: str, document: Document) -> Validation:
    try:
        answer = parse(question, text, document)
    except ParseError as error:
        return Validation(problems=tuple(str(error).split("\n")))
    found = validate(question, answer, document)
    if question.stage is not Stage.JUDGE or not found.valid:
        return found
    return dataclasses.replace(
        found,
        declines=tuple(
            Decline("", None, i.file, i.offset, i.line)
            for i in declines(question, text, document)
        ),
    )


def _flags(rest: tuple[str, ...]) -> set[str]:
    """The flag words in a marker's tokens, up to the `—` a reason follows."""
    upto = rest[: rest.index("—")] if "—" in rest else rest
    return {token for token in upto if token in FLAGS}


def _reason(marker: _Marker) -> str:
    """A declined marker's reason, after its `declined` flag."""
    tokens = list(marker.tokens)
    after = tokens[tokens.index(DECLINED) + 1 :]
    if after and after[0] == "—":
        after = after[1:]
    return " ".join(after) or "declined"


def declines(question: Question, text: str, document: Document) -> list[Insertion]:
    """What a judge declined, from its answer: each declined
    split or join, at its file and offset, with its reason, as the next
    authors' ledger."""
    assert question.workspace is not None
    found = read_markers(text)
    places = places_shown(question, document)
    out: list[Insertion] = []
    for marker in found.markers:
        if not marker.tokens or DECLINED not in _flags(marker.tokens[1:]):
            continue
        if marker.at >= len(places):
            continue
        what = JOIN if marker.tokens[0] == JOINED else SPLIT
        file, offset, _ = places[marker.at]
        out.append(Insertion(file, offset, settled(what, _reason(marker))))
    return out


def _judged_only(reference: _Read, found: _Read) -> None:
    """A judge keeps or declines the author's edits and changes nothing
    else: the same markers at the same places, `declined` only
    on a `proposed` or a `joined` one."""
    problems: list[str] = []
    ours = [(m.at, m.tokens[0]) for m in reference.markers]
    theirs = [(m.at, m.tokens[0] if m.tokens else "") for m in found.markers]
    if theirs != ours:
        stray = [
            m.text
            for m, place in zip(found.markers, theirs, strict=True)
            if place not in ours
        ]
        problems.append(
            "a judge only keeps or declines the author's edits: a marker was "
            "added, deleted or moved"
            + (f" ({', '.join(repr(t) for t in stray[:3])})" if stray else "")
        )
    else:
        for mine, marker in zip(reference.markers, found.markers, strict=True):
            if DECLINED not in _flags(marker.tokens[1:]):
                continue
            if mine.tokens[0] != JOINED and PROPOSED not in _flags(mine.tokens[1:]):
                problems.append(
                    f"line {marker.line}: {mine.tokens[0]} is not an edit of "
                    "the author's: only a 'proposed' or 'joined' marker can "
                    "be declined"
                )
    if problems:
        raise ParseError("\n".join(problems))


def parse(question: Question, edited: str, document: Document) -> dict[str, Any]:
    """The review's answer, `{"corrections": [...]}` by handle, that the
    edit makes: merges, then splits. What follows a handle in a marker is
    not read."""
    edit = boundary_edit(question, edited, document)
    corrections = [_merge(group) for group in edit.merges]
    for block, points in edit.splits.items():
        corrections.append(
            {
                "op": "split",
                "block": block,
                "at": [excerpt for _, excerpt in points],
                "class": "other",
                "why": f"Split in {CUT_FILE}.",
            }
        )
    return {"corrections": corrections}


@dataclass(frozen=True, slots=True)
class Boundaries:
    """What an edit of `cut.md` does to the boundaries, read from its text
    alone: each block marker's place as shown (`start`) and as edited
    (`kept`, where it still stands), the places of new boundaries
    (`news`), the handles whose marker now stands elsewhere (`moved`), the
    runs of handles joined into their first (`merges`), and each handle's
    surviving head (`head_of`)."""

    start: dict[str, int]
    kept: dict[str, int]
    news: frozenset[int]
    moved: frozenset[str]
    merges: list[list[str]]
    head_of: dict[str, str]


def read_boundaries(
    shown: str,
    edited: str,
    top: Sequence[str],
    judged: bool = False,
    shown_only: Callable[[tuple[str, ...]], bool] = lambda tokens: False,
) -> Boundaries:
    """The boundaries an edit of the file `shown` moves, from the text
    alone. `top` is the handles of the cut's top-level blocks in order: a
    nested block (a footnote) has no sibling to join, and the assembler
    merges blocks at one level. A judge's edit (`judged`) only keeps or
    declines. Markers `shown_only` picks, in the file as shown or as
    edited, are not read. Raises `ParseError` with what is wrong."""
    changed = text_kept(shown, edited)
    if changed:
        raise ParseError("\n".join(changed))
    reference = read_markers(shown)
    found = read_markers(edited)
    if judged:
        _judged_only(reference, found)

    problems: list[str] = []
    start: dict[str, int] = {}
    fixed: set[tuple[str, int]] = set()
    for marker in reference.markers:
        if shown_only(marker.tokens):
            continue
        head, rest = marker.tokens[0], marker.tokens[1:]
        if head == CONTEXT or rest == (CONTINUED,):
            fixed.add((" ".join(marker.tokens), marker.at))
        elif head == JOINED:
            continue  # a boundary the author removed: not a block start
        else:
            start[head] = marker.at
    kept: dict[str, int] = {}
    news: set[int] = set()
    seen_fixed: set[tuple[str, int]] = set()
    for marker in found.markers:
        if not marker.tokens:
            problems.append(f"line {marker.line}: an empty marker {marker.text!r}")
            continue
        if shown_only(marker.tokens):
            continue
        head, rest = marker.tokens[0], marker.tokens[1:]
        flags = _flags(rest)
        if head == CONTEXT or rest == (CONTINUED,):
            seen_fixed.add((" ".join(marker.tokens), marker.at))
            continue
        if head == JOINED:
            # A join the author made: no boundary, unless the judge
            # declined it, which puts the boundary back.
            if DECLINED in flags:
                news.add(marker.at)
            continue
        if head in start:
            if DECLINED in flags:
                continue  # the judge reverted this split: the boundary goes
            if head in kept:
                problems.append(f"line {marker.line}: {head} is marked twice")
                continue
            kept[head] = marker.at
        else:
            # `⟦new⟧`, or any handle the file does not have: a new block.
            news.add(marker.at)
    if seen_fixed != fixed:
        problems.append(
            f"a '⟦{CONTEXT}⟧' or '⟦bN {CONTINUED}⟧' line was changed, moved or "
            "removed; these lines are fixed: put it back as it was"
        )

    # A marker at another place: its boundary removed, and a new one there.
    moved: set[str] = set()
    for handle, at in list(kept.items()):
        if at != start[handle]:
            del kept[handle]
            news.add(at)
            moved.add(handle)
    # A new marker where a deleted one stood puts it back.
    restored = {at: handle for handle, at in start.items() if handle not in kept}
    for at in sorted(news):
        if at in restored:
            kept[restored[at]] = at
            moved.discard(restored[at])
            news.discard(at)
    news -= set(kept.values())

    # The merges: a deleted marker joins the block before it.
    # Every problem so far is reported at once: a session's gate gives it
    # a few rounds only.
    order = [handle for handle in top if handle in start]
    merges: list[list[str]] = []
    head_of: dict[str, str] = {}
    group: list[str] = []
    for handle in order:
        if handle in kept:
            if len(group) > 1:
                merges.append(group)
            group = [handle]
        elif not group:
            problems.append(
                f"{handle}'s marker was deleted, but it is the first block of "
                f"{CUT_FILE}: it has no block before it to join"
            )
            group = [handle]
        else:
            group.append(handle)
        head_of[handle] = group[0]
    if len(group) > 1:
        merges.append(group)
    in_order = set(order)
    for handle in start:
        if handle not in kept and handle not in in_order:
            problems.append(
                f"{handle}'s marker was deleted, but it is nested (a footnote "
                "or the like, under another block's text): a nested block is "
                "never joined; put its marker back as it was"
            )
        head_of.setdefault(handle, handle)
    if problems:
        raise ParseError("\n".join(problems))
    return Boundaries(start, kept, frozenset(news), frozenset(moved), merges, head_of)


def top_handles(question: Question) -> list[str]:
    """The handles of the question's top-level blocks, in the cut's order."""
    assert question.input_cut is not None
    names = {block: handle for handle, block in question.handles.items()}
    return [
        names[b.id]
        for b in question.input_cut.blocks
        if b.within is None and b.id in names
    ]


@dataclass(frozen=True, slots=True)
class _Edit:
    """What an edit of `cut.md` does to the boundaries: the runs of
    handles joined into their first (`merges`), and by surviving handle
    each new boundary's place and the excerpt that places it (`splits`)."""

    merges: list[list[str]]
    splits: dict[str, list[tuple[int, str]]]


def boundary_edit(
    question: Question,
    edited: str,
    document: Document,
    shown_only: Callable[[tuple[str, ...]], bool] = lambda tokens: False,
) -> _Edit:
    """The boundaries an edit of `cut.md` moves (`read_boundaries`), each
    new one placed by an excerpt of the paper. Raises `ParseError` with
    what is wrong."""
    # front_lines reads this module's file, so it imports this module
    from axiomation.questions import front_lines

    assert question.workspace is not None and question.input_cut is not None
    shown = front_lines.shown_file(question)
    read = read_boundaries(
        shown,
        edited,
        top_handles(question),
        judged=question.stage is Stage.JUDGE,
        shown_only=shown_only,
    )
    reference = read_markers(shown)
    head_of, news = read.head_of, read.news
    problems: list[str] = []

    places = places_shown(question, document)
    assert len(places) == len(reference.stream), "the render and its places differ"
    handle_of = {block: handle for handle, block in question.handles.items()}
    splits: dict[str, list[tuple[int, str]]] = {}
    for at in sorted(news):
        if at >= len(places):
            continue  # after the last character: no block starts there
        file, offset, block_id = places[at]
        preview = " ".join(document.file(file).text[offset : offset + 60].split()[:6])
        handle = handle_of[block_id]
        if not handle.startswith("b"):
            problems.append(
                f"a new marker before {preview!r} lies in the context, which "
                "is not yours to cut"
            )
            continue
        block = head_of[handle]
        excerpt = excerpt_at(question, document, head_of, block, file, offset)
        if excerpt is None:
            problems.append(
                f"no excerpt from {preview!r} occurs once in {block}: the "
                "split cannot be placed"
            )
            continue
        splits.setdefault(block, []).append((at, excerpt))
    if problems:
        raise ParseError("\n".join(problems))
    return _Edit(read.merges, splits)


def _merge(group: list[str]) -> dict[str, Any]:
    return {
        "op": "merge",
        "blocks": list(group),
        "class": "other",
        "why": f"Joined in {CUT_FILE}.",
    }


def excerpt_at(
    question: Question,
    document: Document,
    head_of: dict[str, str],
    survivor: str,
    file: str,
    offset: int,
) -> str | None:
    """The fewest words of the source from the cut point, three or more,
    that match once in the survivor as the merges leave it, as the
    assembler looks: over its extent, hidden text included, starting on
    a character it owns. Hidden text (a label, a comment) can lie between
    the words, so they are taken from the source, not from the file."""
    assert question.input_cut is not None
    members = [
        question.input_cut.block(question.handles[h])
        for h, head in head_of.items()
        if head == survivor
    ]
    source = document.file(file).text
    start = min(block.extent.start for block in members)
    end = max(block.extent.end for block in members)
    words = source[offset:end].split()
    for n in range(min(EXCERPT_WORDS, len(words)), len(words) + 1):
        pattern = re.compile(
            "(?=(" + r"\s+".join(re.escape(w) for w in words[:n]) + "))"
        )
        starts = [
            start + m.start(1)
            for m in pattern.finditer(source[start:end])
            if any(
                run.file == file and run.start <= start + m.start(1) < run.end
                for block in members
                for run in block.owned
            )
        ]
        if len(starts) == 1:
            return " ".join(words[:n])
    return None
