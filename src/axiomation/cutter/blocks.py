"""The block rules: a document layer to its blocks.

The rules read rendered text, in reading order, one file at a time, and a
block never spans two files. Each file's environments, headings, title,
footnotes and breaks are nested into one tree by their spans; a flow walks a
node's children in order and cuts:

1. the title, a heading, a statement, a float, a code block and an
   unknown environment as one block each (rules 1-3, 7);
2. a proof and a container as transparent: their body is a flow, their
   opening goes to its first block and their closing to its last (rules 4,
   7);
3. a list at its `\\item`s, a nested list's items and a statement in an
   item being blocks of their own (rules 5, 3);
4. a display as its own block, or as part of the prose it touches (rule 6),
   and then of the sentence a blank line breaks at it;
5. the rest as prose, split at blank lines, at `\\paragraph{…}`, and at
   Markdown list items (rules 9, 10); a run-in heading set alone joins the
   paragraph after it, and a piece of bare braces its neighbour.

A footnote is then a block nested in the block around it (rule 8). What a
block owns is its extent less hidden text and less its nested blocks. A
break (an include, the preamble, the end matter, the front matter) ends a
paragraph and belongs to no block.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, auto

from axiomation.cutter.hashing import block_hash
from axiomation.cutter.model import (
    ROLE_OF_MEANING,
    Block,
    CutRules,
    DisplayRule,
    Doubt,
    Form,
    Role,
    Segment,
    segments_of,
)
from axiomation.cutter.text import (
    NOINDENT_RE,
    PARAGRAPH_RE,
    FileView,
    bold_run,
    braced_end,
    indent_of,
    latex_items,
    markdown_item_at,
    views,
)
from axiomation.reader import (
    Document,
    Environment,
    EnvKind,
    FileRole,
    HiddenKind,
    ReadingOrder,
    Span,
)

#: Hidden runs that end a paragraph: nothing on either side of them is one
#: stretch of text.
BREAKS: frozenset[HiddenKind] = frozenset(
    {HiddenKind.PREAMBLE, HiddenKind.END_MATTER, HiddenKind.FRONT_MATTER}
)
#: A Markdown line indented this far continues the list item above it,
#: across a blank line.
CONTINUATION_INDENT = 2
#: What ends a sentence, and what may close around its end: `.)`, `.''`,
#: `$x = 1.$`, `\\(x.\\)`.
SENTENCE_ENDS = frozenset(".!?")
SENTENCE_CLOSERS = frozenset(")]}\"'\u2019\u201d$\\")

_UNIT_FORMS: dict[EnvKind, Form] = {
    EnvKind.STATEMENT: Form.STATEMENT,
    EnvKind.DISPLAY: Form.DISPLAY,
    EnvKind.FLOAT: Form.FLOAT,
    EnvKind.VERBATIM: Form.CODE,
    EnvKind.OTHER: Form.ENVIRONMENT,
}


def _proposed(
    meaning: str | None, form: Form
) -> tuple[tuple[Role, ...], tuple[Segment, ...]]:
    """The cutter's role proposal: one
    segment, the whole block, where the environment names a role."""
    role = ROLE_OF_MEANING.get(meaning or "") if form is Form.STATEMENT else None
    if role is None:
        return (), ()
    return (role,), segments_of((role,))


class _Kind(Enum):
    ENVIRONMENT = auto()
    HEADING = auto()
    TITLE = auto()
    FOOTNOTE = auto()
    BREAK = auto()


@dataclass(eq=False)
class _Node:
    kind: _Kind
    start: int
    end: int
    environment: Environment | None = None
    children: list[_Node] = field(default_factory=list)


@dataclass(eq=False)
class _Draft:
    start: int
    end: int
    form: Form
    environment: str | None = None
    meaning: str | None = None
    doubts: list[Doubt] = field(default_factory=list)
    within: _Draft | None = None
    runs: list[tuple[int, int]] = field(default_factory=list)

    def doubt(self, doubt: Doubt) -> None:
        if doubt not in self.doubts:
            self.doubts.append(doubt)


def cut_blocks(document: Document, rules: CutRules) -> tuple[Block, ...]:
    """Every block of the paper, in reading order."""
    order = ReadingOrder.of(document)
    found: list[Block] = []
    for path, view in views(document).items():
        source = document.file(path)
        if source.role in (FileRole.PREAMBLE, FileRole.PACKAGE):
            continue
        found.extend(_FileCutter(document, view, rules).blocks())
    found.sort(key=lambda block: order.key(block.extent))
    return tuple(found)


def block_id(view: FileView, offset: int) -> str:
    """`<file>:<line>:<column>`, 1-based, of a block's first owned
    character."""
    line = view.line_of(offset)
    return f"{view.path}:{line + 1}:{offset - view.starts[line] + 1}"


class _FileCutter:
    def __init__(self, document: Document, view: FileView, rules: CutRules) -> None:
        self.document = document
        self.view = view
        self.rules = rules
        self.path = view.path

    # --- The tree ---

    def _tree(self) -> _Node:
        path = self.path
        nodes: list[_Node] = []
        for environment in self.document.environments:
            if environment.span.file == path:
                nodes.append(
                    _Node(
                        _Kind.ENVIRONMENT,
                        environment.span.start,
                        environment.span.end,
                        environment,
                    )
                )
        for section in self.document.sections:
            if section.heading.file == path:
                nodes.append(
                    _Node(_Kind.HEADING, section.heading.start, section.heading.end)
                )
        title = self.document.title
        if title is not None and title.file == path:
            nodes.append(_Node(_Kind.TITLE, title.start, title.end))
        for footnote in self.document.footnotes:
            if footnote.at.file == path:
                nodes.append(_Node(_Kind.FOOTNOTE, footnote.at.start, footnote.at.end))
        for include in self.document.includes:
            if include.at.file == path:
                nodes.append(_Node(_Kind.BREAK, include.at.start, include.at.end))
        for hidden in self.document.file(path).hidden:
            if hidden.kind in BREAKS:
                nodes.append(_Node(_Kind.BREAK, hidden.span.start, hidden.span.end))
        nodes = [node for node in nodes if node.end > node.start]
        nodes.sort(key=lambda node: (node.start, -node.end, node.kind.value))
        root = _Node(_Kind.BREAK, 0, len(self.view.text))
        stack = [root]
        for node in nodes:
            while node.start >= stack[-1].end:
                stack.pop()
            if node.end > stack[-1].end:
                # It crosses the node it opens in; it is read as that node's
                # text, which no known paper needs.
                continue
            stack[-1].children.append(node)
            stack.append(node)
        return root

    # --- Blocks ---

    def blocks(self) -> list[Block]:
        root = self._tree()
        drafts = self._flow(root.start, root.end, root.children)
        footnotes = [
            _Draft(node.start, node.end, Form.FOOTNOTE)
            for node in _walk(root)
            if node.kind is _Kind.FOOTNOTE
        ]
        for footnote in footnotes:
            others = [draft for draft in drafts + footnotes if draft is not footnote]
            footnote.within = _innermost(others, footnote.start, footnote.end)
        every = drafts + footnotes
        for draft in every:
            draft.start, draft.end = self.view.trim(draft.start, draft.end)
        for draft in every:
            holes = [
                (child.start, child.end) for child in footnotes if child.within is draft
            ]
            draft.runs = self.view.runs(draft.start, draft.end, holes)
        for footnote in footnotes:
            while footnote.within is not None and not footnote.within.runs:
                footnote.within = footnote.within.within
        kept = sorted(
            (draft for draft in every if draft.runs),
            key=lambda draft: (draft.start, -draft.end),
        )
        self._mid_line(kept)
        return self._finish(kept)

    def _mid_line(self, drafts: Sequence[_Draft]) -> None:
        """Doubt a block that opens on the line where the one before it
        ends, when both render text there."""
        outer = [draft for draft in drafts if draft.within is None]
        for before, after in zip(outer, outer[1:], strict=False):
            gap = self.view.text[before.runs[-1][1] : after.runs[0][0]]
            if "\n" not in gap:
                after.doubt(Doubt.MID_LINE_BOUNDARY)

    def _finish(self, drafts: Sequence[_Draft]) -> list[Block]:
        ids = {id(draft): block_id(self.view, draft.runs[0][0]) for draft in drafts}
        blocks: list[Block] = []
        for draft in drafts:
            roles, segments = _proposed(draft.meaning, draft.form)
            blocks.append(
                Block(
                    id=ids[id(draft)],
                    extent=self.view.span(draft.start, draft.end),
                    owned=tuple(
                        self.view.span(start, end) for start, end in draft.runs
                    ),
                    within=ids[id(draft.within)] if draft.within is not None else None,
                    form=draft.form,
                    environment=draft.environment,
                    meaning=draft.meaning,
                    roles=roles,
                    segments=segments,
                    hash=block_hash(
                        self.view.text[start:end] for start, end in draft.runs
                    ),
                    doubts=tuple(draft.doubts),
                )
            )
        return blocks

    # --- The flow ---

    def _inline(self, node: _Node) -> bool:
        """A node that sits inside a paragraph rather than ending it."""
        if node.kind is _Kind.FOOTNOTE:
            return True
        return (
            self.rules.displays is DisplayRule.IN_PARAGRAPH
            and node.environment is not None
            and node.environment.kind is EnvKind.DISPLAY
        )

    def _flow(self, start: int, end: int, children: Sequence[_Node]) -> list[_Draft]:
        drafts: list[_Draft] = []
        cursor = start
        inline: list[_Node] = []
        after_display: int | None = None
        for child in children:
            if child.start < start or child.end > end:
                continue
            if self._inline(child):
                inline.append(child)
                continue
            drafts.extend(self._prose(cursor, child.start, inline, after_display))
            inline = []
            drafts.extend(self._node(child))
            environment = child.environment
            after_display = (
                child.end
                if environment is not None and environment.kind is EnvKind.DISPLAY
                else None
            )
            cursor = child.end
        drafts.extend(self._prose(cursor, end, inline, after_display))
        return self._without_bare_braces(drafts)

    def _without_bare_braces(self, drafts: list[_Draft]) -> list[_Draft]:
        """A prose piece that renders only braces closes or opens a group
        around its neighbour, and renders nothing in LaTeX: it joins the
        block before it, or the one after when it comes first (the `}` of
        `\\end{defn}}`)."""
        kept: list[_Draft] = []
        pending: _Draft | None = None
        for draft in drafts:
            text = self.view.rendered[draft.start : draft.end]
            bare = (
                draft.form is Form.PROSE
                and not text.replace("{", "").replace("}", "").strip()
            )
            if bare and kept:
                kept[-1].end = draft.end
            elif bare:
                pending = draft
            else:
                if pending is not None:
                    draft.start = pending.start
                    pending = None
                kept.append(draft)
        if pending is not None:
            kept.append(pending)
        return kept

    def _node(self, node: _Node) -> list[_Draft]:
        if node.kind is _Kind.BREAK:
            return []
        if node.kind is _Kind.TITLE:
            return [_Draft(node.start, node.end, Form.TITLE)]
        if node.kind is _Kind.HEADING:
            return [_Draft(node.start, node.end, Form.HEADING)]
        environment = node.environment
        assert environment is not None
        kind = environment.kind
        if kind in (EnvKind.PROOF, EnvKind.CONTAINER):
            drafts = self._flow(
                environment.body.start, environment.body.end, node.children
            )
            if not drafts:
                drafts = [_Draft(node.start, node.end, Form.PROSE)]
            drafts[0].start = node.start
            drafts[-1].end = node.end
        elif kind is EnvKind.LIST:
            drafts = self._list(node)
        else:
            form = _UNIT_FORMS[kind]
            drafts = [
                _Draft(
                    node.start,
                    node.end,
                    form,
                    environment=environment.name,
                    meaning=environment.meaning,
                )
            ]
            if kind is EnvKind.OTHER:
                drafts[0].doubt(Doubt.UNKNOWN_ENVIRONMENT)
        if not environment.closed:
            drafts[0].doubt(Doubt.UNCLOSED_ENVIRONMENT)
        return drafts

    def _list(self, node: _Node) -> list[_Draft]:
        environment = node.environment
        assert environment is not None
        body = environment.body
        kids = [kid for kid in node.children if kid.start >= body.start]
        skip = [(kid.start, kid.end) for kid in kids]
        starts = [
            item.start for item in latex_items(self.view, body.start, body.end, skip)
        ]
        name = environment.name

        def item(start: int, end: int) -> _Draft:
            return _Draft(start, end, Form.ITEM, environment=name)

        drafts: list[_Draft] = []
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else body.end
            cursor = start
            for kid in kids:
                if not start <= kid.start < end:
                    continue
                environment_kid = kid.environment
                if environment_kid is None:
                    continue
                if environment_kid.kind is EnvKind.LIST:
                    drafts.append(item(cursor, kid.start))
                    drafts.extend(self._list(kid))
                elif environment_kid.kind is EnvKind.STATEMENT:
                    # A statement in an item is one block (rule 3); the
                    # item's text after it is a further block.
                    drafts.append(item(cursor, kid.start))
                    drafts.extend(self._node(kid))
                else:
                    continue
                cursor = kid.end
            drafts.append(item(cursor, end))
        drafts = [
            draft for draft in drafts if self.view.renders(draft.start, draft.end)
        ]
        if not drafts:
            return [item(node.start, node.end)]
        drafts[0].start = node.start
        drafts[-1].end = node.end
        return drafts

    # --- Prose ---

    def _prose(
        self,
        start: int,
        end: int,
        inline: Sequence[_Node],
        after_display: int | None,
    ) -> list[_Draft]:
        """The paragraphs of `start`..`end`: split at blank lines, before a
        `\\paragraph{…}` and before a Markdown list item, never inside an
        inline node."""
        if start >= end:
            return []
        view = self.view
        opaque = [(node.start, node.end) for node in inline]

        def covered(offset: int) -> bool:
            return any(first <= offset < last for first, last in opaque)

        pieces: list[list[int]] = []  # [start, end, blank_before]
        cuts: list[tuple[int, bool]] = []  # (offset, after a blank line)
        first_line, last_line = view.line_of(start), view.line_of(max(end - 1, start))
        in_blank = False
        for line in range(first_line, last_line + 1):
            line_start = max(view.starts[line], start)
            if view.blank[line] and not covered(view.starts[line]):
                in_blank = True
                continue
            if in_blank:
                cuts.append((line_start, True))
                in_blank = False
            item = markdown_item_at(view, line)
            if (
                item is not None
                and start <= item.start < end
                and not covered(item.start)
            ):
                cuts.append((max(item.line_start, start), False))
        for match in PARAGRAPH_RE.finditer(view.rendered, start, end):
            if not covered(match.start()):
                cuts.append((match.start(), False))
        cuts.sort()
        boundary = start
        blank_before = False
        for offset, blank in cuts:
            if offset > boundary:
                pieces.append([boundary, offset, blank_before])
                boundary = offset
                blank_before = blank
            else:
                blank_before = blank_before or blank
        pieces.append([boundary, end, blank_before])

        drafts: list[_Draft] = []
        joining = False
        #: The marker indent of the Markdown item each ITEM draft continues.
        indents: dict[int, int] = {}
        for piece_start, piece_end, blank in pieces:
            if not view.renders(piece_start, piece_end):
                continue
            first_line = view.line_of(view.trim(piece_start, piece_end)[0])
            item_line = markdown_item_at(view, first_line)
            if drafts and joining:
                drafts[-1].end = piece_end
                joining = self._only_paragraph_heading(drafts[-1])
                continue
            indent = indent_of(view, first_line)
            last = drafts[-1] if drafts else None
            continued = (
                last is not None
                and blank
                and last.form is Form.ITEM
                and item_line is None
                and indent >= CONTINUATION_INDENT
            )
            if continued and indent > indents.get(id(last), 0):
                last.end = piece_end
                continue
            if continued:
                # Indented to an outer item, past a nested one: a further
                # block of the outer item (rule 5).
                draft = _Draft(piece_start, piece_end, Form.ITEM)
                indents[id(draft)] = max(
                    (value for value in indents.values() if value < indent),
                    default=0,
                )
                drafts.append(draft)
                continue
            if (
                last is not None
                and blank
                and last.form is Form.PROSE
                and item_line is None
                and self.rules.displays is DisplayRule.IN_PARAGRAPH
            ):
                rest = self._sentence_across(last, piece_start, piece_end, inline)
                if rest is None:
                    continue
                piece_start = rest
            draft = _Draft(
                piece_start,
                piece_end,
                Form.ITEM if item_line is not None else Form.PROSE,
            )
            if item_line is not None:
                indents[id(draft)] = item_line.indent
            drafts.append(draft)
            joining = self._only_paragraph_heading(draft)
        for draft in drafts:
            if draft.form is Form.PROSE:
                display = self._only_display(draft, inline)
                if display is not None:
                    draft.form = Form.DISPLAY
                    draft.environment = display.name
                    draft.meaning = display.meaning
        if (
            drafts
            and after_display is not None
            and self.rules.displays is DisplayRule.OWN_BLOCK
            and not any(
                view.blank[line]
                for line in range(
                    view.line_of(after_display),
                    view.line_of(view.trim(drafts[0].start, drafts[0].end)[0]) + 1,
                )
            )
        ):
            drafts[0].doubt(Doubt.DISPLAY_IN_SENTENCE)
        return drafts

    def _sentence_across(
        self, last: _Draft, start: int, end: int, inline: Sequence[_Node]
    ) -> int | None:
        """Where the piece `start`..`end` after a blank line begins its block,
        when the blank line may fall inside a sentence at a display. A paragraph
        that ends mid-sentence takes the displays the piece
        opens with, and the piece begins after them; a piece that opens in
        lower case after a display continues its sentence. `None` when
        `last` takes the whole piece."""
        tail, display = self._edge(last.start, last.end, inline, from_end=True)
        if tail is None:
            return start
        if display is None and self._sentence_ends(tail):
            return start
        cursor = start
        while True:
            head, opening = self._edge(cursor, end, inline, from_end=False)
            if head is None:
                last.end = end
                return None
            if opening is not None and display is None:
                last.end = cursor = opening.end
                continue
            after_display = display is not None or cursor > start
            if after_display and opening is None and self.view.rendered[head].islower():
                last.end = end
                return None
            return cursor

    def _edge(
        self, start: int, end: int, inline: Sequence[_Node], from_end: bool
    ) -> tuple[int | None, _Node | None]:
        """The first (or last) character `start`..`end` renders outside its
        footnotes, and the display that holds it, if one does."""
        text = self.view.rendered
        offsets = range(end - 1, start - 1, -1) if from_end else range(start, end)
        skip_to: int | None = None
        for offset in offsets:
            if skip_to is not None and (
                offset >= skip_to if from_end else offset < skip_to
            ):
                continue
            skip_to = None
            if text[offset].isspace():
                continue
            node = next(
                (node for node in inline if node.start <= offset < node.end), None
            )
            if node is not None and node.kind is _Kind.FOOTNOTE:
                skip_to = node.start if from_end else node.end
                continue
            return offset, node
        return None, None

    def _sentence_ends(self, offset: int) -> bool:
        """Whether the text up to `offset` ends a sentence: `.`, `!` or `?`,
        before any closing brackets and quotes."""
        text = self.view.rendered
        while offset > 0 and (
            text[offset] in SENTENCE_CLOSERS or text[offset].isspace()
        ):
            offset -= 1
        return text[offset] in SENTENCE_ENDS

    def _only_paragraph_heading(self, draft: _Draft) -> bool:
        """Whether a piece renders nothing but a `\\paragraph{…}` heading,
        or a bold run set as a paragraph of its own
        (`\\textbf{Step 5: …}`). An italic paragraph is emphasis, not a
        heading: it stays a block."""
        text = self.view.rendered
        start, end = self.view.trim(draft.start, draft.end)
        match = PARAGRAPH_RE.match(text, start)
        if match is not None:
            closing = braced_end(text, match.end())
            return closing is not None and not self.view.renders(closing, end)
        noindent = NOINDENT_RE.match(text, start)
        if noindent is not None:
            start = noindent.end()
        run = bold_run(text, start)
        return run is not None and not self.view.renders(run.end, end)

    def _only_display(
        self, draft: _Draft, inline: Sequence[_Node]
    ) -> Environment | None:
        """The display a piece holds, when it renders nothing else."""
        for node in inline:
            environment = node.environment
            if environment is None or environment.kind is not EnvKind.DISPLAY:
                continue
            if not (draft.start <= node.start and node.end <= draft.end):
                continue
            view = self.view
            if not view.renders(draft.start, node.start) and not view.renders(
                node.end, draft.end
            ):
                return environment
        return None


def _walk(node: _Node):
    for child in node.children:
        yield child
        yield from _walk(child)


def _innermost(drafts: Sequence[_Draft], start: int, end: int) -> _Draft | None:
    best: _Draft | None = None
    for draft in drafts:
        inside = draft.start <= start and end <= draft.end
        if inside and (best is None or draft.end - draft.start < best.end - best.start):
            best = draft
    return best


class BlockIndex:
    """Where the blocks of one cut stand: which block owns a character, and
    which holds a span (the innermost whose extent contains it)."""

    def __init__(self, blocks: Sequence[Block]) -> None:
        self.blocks = tuple(blocks)
        self.by_id = {block.id: block for block in self.blocks}
        self.position = {block.id: index for index, block in enumerate(self.blocks)}
        self._runs: dict[str, list[tuple[int, int, str]]] = {}
        self._extents: dict[str, list[Block]] = {}
        for block in self.blocks:
            for run in block.owned:
                self._runs.setdefault(run.file, []).append(
                    (run.start, run.end, block.id)
                )
            self._extents.setdefault(block.extent.file, []).append(block)
        for runs in self._runs.values():
            runs.sort()
        self._starts = {
            path: [run[0] for run in runs] for path, runs in self._runs.items()
        }

    def owner(self, file: str, offset: int) -> str | None:
        """The block that owns the character at `offset`, else the block that
        holds it."""
        runs = self._runs.get(file, [])
        index = bisect.bisect_right(self._starts.get(file, []), offset) - 1
        if index >= 0 and runs[index][0] <= offset < runs[index][1]:
            return runs[index][2]
        return self.holder(Span(file, offset, offset + 1))

    def holder(self, span: Span) -> str | None:
        """The innermost block whose extent contains `span`."""
        best: Block | None = None
        for block in self._extents.get(span.file, []):
            extent = block.extent
            if extent.start <= span.start and span.end <= extent.end:
                size = extent.end - extent.start
                if best is None or size < best.extent.end - best.extent.start:
                    best = block
        return best.id if best is not None else None

    def overlapping(self, span: Span) -> list[str]:
        """The blocks whose extent meets `span`, in reading order."""
        return [
            block.id
            for block in self._extents.get(span.file, [])
            if block.extent.start < span.end and span.start < block.extent.end
        ]
