"""The roles as a session on one file, per chunk.

A chunk's blocks are written out as `roles.md`, the cut paper the way
`cut.md` is (`cut_file.render`), each block under a bare marker `⟦b12⟧`:
no role, so the session never sees one a block carries (the cutter's
proposal or a key's), and no description. A first pass and a touched
session read it as `paper.md` and answer as lines, one a block
(`front_lines`): `b13 justification | claim "Hence the bound"`. The
lines are laid into the file: each block's marker gets the role of the
block's first part (`none` for a block with none), and a line
`⟦+ <role>⟧` stands where each later part starts. A later pass edits the
file itself, its markers showing the roles the cut carries.
A block the cut holds with several roles and no part (`docs/model.md`
§ 4) shows them all in its marker, with a note that a writer names one,
and reads back so.
The cut is frozen: the text, the `##### <file>` lines and every block,
context and continued marker must stay where they are.

The answer is the edited `roles.md`, or the lines laid into it. Each block
of the chunk becomes one `ROLES` correction: the roles its segments carry,
and the segments, each later one starting at an excerpt placed as a
split's is (`cut_file.excerpt_at`). An answer is used whole or not at all.

Under a block's marker the session may add a flag line, `⟦? <signal>
"<the block's words>" — <note>⟧`: a position on the cut or an earlier
step, which changes nothing and a later step reads.

The key is the kind, the stage, the template and the chunk's blocks as the
file shows them, less their roles; the context blocks are context.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace

from axiomation.cutter.model import Block, Cut, Role, Segment, role_set
from axiomation.questions import (
    Question,
    Stage,
    Template,
    Validation,
    Workspace,
    front_lines,
)
from axiomation.questions.chunks import DEFAULT_OVERLAP, Chunk, chunks
from axiomation.questions.cut_file import (
    CONTEXT,
    CONTINUED,
    Insertion,
    ParseError,
    RenderError,
    as_flags,
    block_inputs,
    excerpt_at,
    fixed_lines,
    flag_lines,
    is_flag,
    places_shown,
    read_markers,
    render,
    text_kept,
)
from axiomation.questions.cut_review import (
    applied_alone,
    labels_by_block,
    question_key,
)
from axiomation.reader import Document
from axiomation.records import (
    STAGE_SIGNALS,
    CorrectionClass,
    CutCorrection,
    CutOp,
    Flag,
)

KIND = "roles"
#: The first pass's and a touched session's brief: answered as lines.
SESSION_TEMPLATE = "roles-lines"
#: A later pass reviews the roles the passes before it left.
REVIEW_TEMPLATE = "roles-review"
ROLES_FILE = "roles.md"
KICKOFF = (
    "Write the role of every block in paper.md in roles.md, as your instructions say."
)
REVIEW_KICKOFF = "Review the roles in roles.md, as your instructions say."

#: The head of a line that starts a segment inside a block.
SEGMENT = "+"
#: The role word of a block that plays none.
NONE = "none"
#: What a later pass's marker says after its roles where the cut holds the
#: block with several and no part: a form read back as held, never one a
#: writer gives another block. No `⟧` in it: that would end the marker.
UNSEGMENTED_NOTE = (
    "held so, with no parts; an edit names one role here, its first part's, "
    "and starts each later part at a + <role> marker line"
)

SESSION_WHY = "the roles session"


def session_questions(
    document: Document,
    cut: Cut,
    budget: int | None,
    template: Template,
    overlap: int = DEFAULT_OVERLAP,
    pass_: int = 1,
    notes: Mapping[str, str] | None = None,
) -> tuple[Question, ...]:
    """One session per chunk, in reading order, as the boundary stage
    chunks the paper. A first pass shows bare markers in `paper.md` and is
    answered as lines (`front_lines`). A later pass
    (`pass_` 2 or more) shows the roles the cut carries, each block's first
    part's in its marker and a `⟦+ <role>⟧` line where each later part
    starts, for the session to review; the pass and those roles are part
    of its key. `notes`, by block id, follow a later pass's marker after
    ` — ` (a person's file: where a role came from), and
    `UNSEGMENTED_NOTE` a block's held with several roles and no part;
    the answer's reading passes over them."""
    review = pass_ > 1
    noted = notes or {}

    def marker(block: Block) -> str:
        said = [noted[block.id]] if block.id in noted else []
        if not block.segments and len(block.roles) > 1:
            said.append(UNSEGMENTED_NOTE)
        word = first_role(block)
        return f"{word} — {'; '.join(said)}" if said else word

    labels = labels_by_block(document, cut)
    questions = []
    for chunk in chunks(document, cut, budget, overlap):
        handles = {block: f"b{n}" for n, block in enumerate(chunk.blocks, 1)}
        inputs = []
        for block_id in chunk.blocks:
            shown = block_inputs(
                document, cut, cut.block(block_id), handles[block_id], handles, labels
            )
            del shown["role"]
            inputs.append(shown)
        key_inputs: dict[str, object] = {"chunk": inputs}
        if review:
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
                label=marker,
            )
            key_inputs["pass"] = pass_
            key_inputs["roles"] = [
                [
                    {"starts": s.starts, "role": s.role.value if s.role else NONE}
                    for s in cut.block(block_id).segments
                ]
                or [r.value for r in cut.block(block_id).roles]
                for block_id in chunk.blocks
            ]
        else:
            rendered = render(
                document, cut, chunk, handles, labels, label=lambda block: ""
            )
        questions.append(
            _question(
                cut,
                template,
                key_inputs,
                rendered,
                handles,
                [*chunk.before, *chunk.after],
                REVIEW_KICKOFF if review else KICKOFF,
                lines=not review,
            )
        )
    return tuple(questions)


def touched_question(
    document: Document,
    cut: Cut,
    blocks: Sequence[str],
    template: Template,
    overlap: int = DEFAULT_OVERLAP,
    round_: int = 1,
    notes: Mapping[str, str] | None = None,
) -> Question:
    """The roles of the blocks a cut correction changed, or a
    later step flagged: one session on those blocks alone,
    shown and answered as a first pass is, each with `overlap` blocks on either
    side as context. The paper's chunk is most often the whole paper, so
    asking its chunk again would ask every block. The key is a first
    pass's over these blocks, with the round; `notes`, by block, what a
    later step read in a block's roles, are shown in the prompt and bound
    in the key where there are any."""
    changed = set(blocks)
    order = {block.id: n for n, block in enumerate(cut.blocks)}
    near = {
        cut.blocks[i].id
        for block_id in changed
        for i in range(
            max(order[block_id] - overlap, 0),
            min(order[block_id] + overlap + 1, len(cut.blocks)),
        )
    }
    shown = sorted(changed | near, key=order.__getitem__)
    ours = [b for b in shown if b in changed]
    handles = {block: f"b{n}" for n, block in enumerate(ours, 1)}
    labels = labels_by_block(document, cut)
    inputs = []
    for block_id in ours:
        found = block_inputs(
            document, cut, cut.block(block_id), handles[block_id], handles, labels
        )
        del found["role"]
        inputs.append(found)
    rendered = render(
        document,
        cut,
        Chunk(tuple(shown)),
        handles,
        labels,
        label=lambda block: "",
        context=lambda block: f"roles: {_settled_roles(block)}",
    )
    key_inputs: dict[str, object] = {
        "chunk": inputs,
        "round": round_,
        # what the context is; the context itself is not bound
        "context": CONTEXT_NOTE,
    }
    prompt = KICKOFF
    noted = {handles[b]: note for b, note in (notes or {}).items() if b in handles}
    if noted:
        key_inputs["notes"] = noted
        prompt += (
            "\n\nA later step read these blocks' roles as follows; weigh it:\n"
            + ("\n".join(f"- {h}: {note}" for h, note in noted.items()))
        )
    return _question(
        cut,
        template,
        key_inputs,
        rendered,
        handles,
        [b for b in shown if b not in changed],
        prompt,
        CONTEXT_NOTE,
        lines=True,
    )


#: What a touched session's context is, so it answers no role for it.
CONTEXT_NOTE = (
    "Text under a `⟦context⟧` line is the paper just before or after your "
    "blocks, there to read, not to answer: its blocks are outside this "
    "session and their roles are settled. Write no line for it."
)


def _settled_roles(block: Block) -> str:
    """A context block's roles as the touched session shows them."""
    return ", ".join(r.value for r in block.roles) or NONE


def _question(
    cut: Cut,
    template: Template,
    key_inputs: dict[str, object],
    rendered: str,
    handles: dict[str, str],
    context: Sequence[str],
    prompt: str,
    note: str = "",
    lines: bool = False,
) -> Question:
    """A roles session: `handles` its blocks' (`b<n>`), `context` the
    blocks shown around them (`c<n>`); `note` follows the brief. With
    `lines`, the session reads `rendered` as `paper.md` and answers as
    lines; else it edits it as `roles.md`."""
    instructions = template.render() + (f"\n\n{note}\n" if note else "")
    if lines:  # a brief for lines and one for the file never share a key
        key_inputs = {**key_inputs, "lines": True}
    return Question(
        key=question_key(Stage.ROLES, template, key_inputs, KIND),
        kind=KIND,
        stage=Stage.ROLES,
        template=template,
        prompt=prompt,
        handles={
            **{handle: block for block, handle in handles.items()},
            **{f"c{n}": block for n, block in enumerate(context, 1)},
        },
        input_cut=cut,
        workspace=front_lines.workspace(Stage.ROLES, rendered, instructions)
        if lines
        else Workspace(
            files={ROLES_FILE: rendered},
            edit=ROLES_FILE,
            instructions=instructions,
            reask_from_edit=True,
        ),
    )


def first_role(block: Block) -> str:
    """A block's marker word in a review: its first part's role, `none`
    for a block that plays none; the roles, where a merge left them
    unsegmented."""
    if block.segments:
        role = block.segments[0].role
        return role.value if role else NONE
    return ", ".join(r.value for r in block.roles) or NONE


def segment_starts(
    text: str, start: int, end: int, owned: Sequence[tuple[int, int]], words: str
) -> list[int]:
    """Where a segment's excerpt `words` matches in `text[start:end]`, its
    whitespace free, each match starting on a character the block owns
    (`owned`, its runs' offsets): a later part starts at the one match."""
    pattern = re.compile(
        "(?=(" + r"\s+".join(re.escape(w) for w in words.split()) + "))"
    )
    return [
        start + m.start(1)
        for m in pattern.finditer(text[start:end])
        if any(a <= start + m.start(1) < b for a, b in owned)
    ]


def part_lines(document: Document, block: Block) -> list[Insertion]:
    """A `⟦+ <role>⟧` line where each of the block's later parts starts: at
    its excerpt's one match that starts on a character the block owns, as
    the assembler places it."""
    text = document.file(block.extent.file).text
    owned = [(run.start, run.end) for run in block.owned]
    found = []
    for segment in block.segments[1:]:
        starts = segment_starts(
            text, block.extent.start, block.extent.end, owned, segment.starts
        )
        if len(starts) != 1:
            raise RenderError(
                f"{block.id}: its part starting {segment.starts!r} matches "
                f"{len(starts)} times in its owned text, not once"
            )
        role = segment.role.value if segment.role else NONE
        found.append(Insertion(block.extent.file, starts[0], f"⟦{SEGMENT} {role}⟧"))
    return found


# --- the answer ---------------------------------------------------------------------


def _as_shown(shown: str, edited: str) -> set[str]:
    """The blocks whose markers `edited` left as `shown` has them, where the file
    shows their roles: the state's, not the edit's, so they are not judged
    again and keep the roles and parts the cut holds (`docs/model.md` § 7).
    A block shown with no role yet is the session's to answer."""

    def said(text: str) -> dict[str, list[tuple[int, tuple[str, ...]]]]:
        by_block: dict[str, list[tuple[int, tuple[str, ...]]]] = {}
        current: str | None = None
        for marker in read_markers(text).markers:
            if marker.tokens and is_flag(marker.tokens):
                continue
            head = marker.tokens[0] if marker.tokens else ""
            if marker.tokens[1:] == (CONTINUED,):
                current = head  # a block's text again, after a nested one
                by_block.setdefault(head, []).append((marker.at, marker.tokens))
            elif head and head not in (SEGMENT, CONTEXT):
                current = head
                by_block[head] = [(marker.at, marker.tokens)]
            elif current is not None:
                by_block[current].append((marker.at, marker.tokens))
        return by_block

    def names_roles(tokens: tuple[str, ...]) -> bool:
        rest = tokens[1:]
        return bool(rest[: rest.index("—")] if "—" in rest else rest)

    before, after = said(shown), said(edited)
    return {
        h
        for h, markers in after.items()
        if before.get(h) == markers and names_roles(markers[0][1])
    }


def _held_unsegmented(question: Question, head: str, words: Sequence[str]) -> bool:
    """Whether `words`, a marker's several roles, are the roles the cut holds
    `head` with and no part: a standing's claim or a merge leaves a block so
    (`docs/model.md` § 4), and the file says it as the cut holds it. A
    writer gives each part's role; a block is never made unsegmented here."""
    assert question.input_cut is not None
    if head not in question.handles:
        return False
    try:
        named = {_role(w) for w in words}
    except ValueError:
        return False
    block = question.input_cut.block(question.handles[head])
    return not block.segments and named == set(block.roles)


def kept_problems(question: Question, document: Document) -> tuple[str, ...]:
    """What the roles file as `question` shows it is refused for, every
    block judged: a kept state's own problems, which no edit is refused
    for (`_as_shown`)."""
    shown = front_lines.shown_file(question)
    try:
        parse(question, shown, document, kept=())
    except ParseError as error:
        return tuple(str(error).split("\n"))
    return ()


def check(question: Question, text: str, document: Document) -> Validation:
    """An edited `roles.md`, or the lines laid into it (`front_lines`),
    parsed, and its corrections applied alone to the question's cut, with
    its flags."""
    return front_lines.checked(
        question, text, lambda f: _checked(question, f, document)
    )


def _checked(question: Question, text: str, document: Document) -> Validation:
    try:
        corrections = parse(question, text, document)
        found = flags(question, text, document)
    except ParseError as error:
        return Validation(problems=tuple(str(error).split("\n")))
    checked = applied_alone(question, corrections, document)
    return replace(checked, flags=found) if checked.valid else checked


def flags(question: Question, edited: str, document: Document) -> tuple[Flag, ...]:
    """The flags of an edited `roles.md`. Raises `ParseError`
    with what is wrong with any."""
    assert question.workspace is not None
    handle_of = {block: handle for handle, block in question.handles.items()}
    owners = [handle_of[block] for _, _, block in places_shown(question, document)]
    lines, problems = flag_lines(
        front_lines.shown_file(question),
        edited,
        owners,
        STAGE_SIGNALS[Stage.ROLES.value],
    )
    if problems:
        raise ParseError("\n".join(problems))
    return as_flags(lines, question.handles)


def _block_line(tokens: tuple[str, ...]) -> tuple[str, bool] | None:
    """A marker's place in the fixed cut: its head and whether it
    continues a block; `None` for a part's line or a flag line, the
    session's to add."""
    if tokens and tokens[0] == SEGMENT or is_flag(tokens):
        return None
    return (tokens[0] if tokens else "", tokens[1:] == (CONTINUED,))


def _role(word: str) -> Role | None:
    """A marker's role word as a role; `None` for `none`. Raises
    `ValueError` for any other word."""
    word = word.strip(",.;:").lower()
    if word == NONE:
        return None
    return Role(word)


def parse(
    question: Question,
    edited: str,
    document: Document,
    kept: Collection[str] | None = None,
) -> list[CutCorrection]:
    """The `ROLES` corrections the edit gives, one per block of the chunk,
    in reading order; a block in `kept` (by default, those whose markers
    the edit left as shown, `_as_shown`) is not judged and keeps the cut's
    roles."""
    assert question.workspace is not None and question.input_cut is not None
    shown = front_lines.shown_file(question)
    changed = text_kept(shown, edited) + fixed_lines(
        shown,
        edited,
        _block_line,
        "the blocks' lines",
        f"is not a block's line: mark where a part starts with a line "
        f"'⟦{SEGMENT} <role>⟧', flag a block with a line '⟦? <signal> "
        f'"<its words>" — <note>⟧\', or remove it',
    )
    if changed:
        raise ParseError("\n".join(changed))
    found = read_markers(edited)
    kept = _as_shown(shown, edited) if kept is None else kept
    # a marker as shown on a kept block is the state's; one added there is
    # the edit's, and judged
    as_shown = {(m.at, m.tokens) for m in read_markers(shown).markers}

    problems: list[str] = []
    first: dict[str, Role | None] = {}
    unsegmented: set[str] = set()
    for marker in found.markers:
        if not marker.tokens:
            problems.append(f"line {marker.line}: an empty marker {marker.text!r}")
            continue
        head, rest = marker.tokens[0], marker.tokens[1:]
        if head in (SEGMENT, CONTEXT) or rest == (CONTINUED,) or is_flag(marker.tokens):
            continue
        if head in kept:
            continue
        words = list(rest[: rest.index("—")] if "—" in rest else rest)
        if not words:
            problems.append(f"line {marker.line}: {head} has no role yet")
            continue
        if len(words) > 1:
            if _held_unsegmented(question, head, words):
                unsegmented.add(head)
                continue
            problems.append(
                f"line {marker.line}: {head} names {len(words)} roles; name the "
                f"role of its first span, and mark each change with a line "
                f"'⟦{SEGMENT} <role>⟧' (a marker shows several roles only where "
                "the cut holds a block so, with no parts)"
            )
            continue
        try:
            first[head] = _role(words[0])
        except ValueError:
            problems.append(f"line {marker.line}: {words[0]!r} is not a role")

    places = places_shown(question, document)
    handle_of = {block: handle for handle, block in question.handles.items()}
    later: dict[str, list[tuple[str, Role | None]]] = {}
    for marker in found.markers:
        if not marker.tokens or marker.tokens[0] != SEGMENT:
            continue
        words = marker.tokens[1:]
        if len(words) != 1:
            problems.append(f"line {marker.line}: '⟦{SEGMENT} <role>⟧' names one role")
            continue
        try:
            role = _role(words[0])
        except ValueError:
            problems.append(f"line {marker.line}: {words[0]!r} is not a role")
            continue
        if marker.at >= len(places):
            problems.append(f"line {marker.line}: no text follows it")
            continue
        file, offset, block_id = places[marker.at]
        handle = handle_of[block_id]
        if handle in kept and (marker.at, marker.tokens) in as_shown:
            continue
        if handle in unsegmented:
            problems.append(
                f"line {marker.line}: {handle}'s marker names the roles it holds "
                "unsegmented; to mark its parts, name its first part's role there"
            )
            continue
        if not handle.startswith("b"):
            problems.append(f"line {marker.line}: it lies in the context")
            continue
        block = question.input_cut.block(block_id)
        if offset == block.owned[0].start:
            problems.append(
                f"line {marker.line}: it starts {handle}; give the first span's "
                "role in the block's marker"
            )
            continue
        excerpt = excerpt_at(question, document, {handle: handle}, handle, file, offset)
        if excerpt is None:
            problems.append(
                f"line {marker.line}: no excerpt from there occurs once in "
                f"{handle}: the span cannot be placed"
            )
            continue
        later.setdefault(handle, []).append((excerpt, role))
    if problems:
        raise ParseError("\n".join(problems))

    corrections = []
    for handle in [h for h in question.handles if h.startswith("b")]:
        if handle in kept or handle in unsegmented:
            block = question.input_cut.block(question.handles[handle])
            corrections.append(
                CutCorrection(
                    id="",
                    op=CutOp.ROLES,
                    cls=CorrectionClass.OTHER,
                    why=SESSION_WHY,
                    block=block.id,
                    roles=block.roles,
                    segments=block.segments,
                )
            )
            continue
        spans = later.get(handle, [])
        segments: tuple[Segment, ...] = (
            Segment("", first.get(handle)),
            *(Segment(e, r) for e, r in spans),
        )
        same = [
            b.role.value if b.role else NONE
            for a, b in zip(segments, segments[1:], strict=False)
            if a.role is b.role
        ]
        if same:
            problems.append(
                f"{handle}: a '⟦{SEGMENT} {same[0]}⟧' line continues the role "
                "before it; add one only where the role changes"
            )
            continue
        if all(s.role is None for s in segments):
            segments = ()  # a block that plays none
        corrections.append(
            CutCorrection(
                id="",
                op=CutOp.ROLES,
                cls=CorrectionClass.OTHER,
                why=SESSION_WHY,
                block=question.handles[handle],
                roles=role_set(s.role for s in segments),
                segments=segments,
            )
        )
    if problems:
        raise ParseError("\n".join(problems))
    return corrections
