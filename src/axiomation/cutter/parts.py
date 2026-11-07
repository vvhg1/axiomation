"""Marked parts: what the text marks inside a block.

An entry is anchored to the text's own marker where there is one. The parts are
those markers, proposed on every block, whether or not an edge ever needs
them:

- **items:** every `\\item` and Markdown list item, in a statement or not;
- **labelled displays:** each `\\label` in a display, one per labelled row
  of an `align`, and a label written just before a display that has none
 ;
- **headed:** a `\\paragraph{…}`, or a run-in heading that opens a block or
  an `\\item`: a bold run (`\\textbf`, `{\\bf …}`, `**…**`), or a short
  italic one that ends in `.`, `:` or `.)` with text after it.
  A step heading is a headed part whose marker names a step.
- Every part lies inside its block: an item a statement cuts is marked up
  to the statement.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from axiomation.cutter.blocks import CONTINUATION_INDENT, BlockIndex
from axiomation.cutter.model import Block, Form, MarkedPart, PartKind
from axiomation.cutter.text import (
    PARAGRAPH_RE,
    FileView,
    RunIn,
    bold_run,
    braced_end,
    bracketed_end,
    indent_of,
    italic_run_in,
    latex_items,
    markdown_item_at,
    plain,
    views,
)
from axiomation.reader import Document, Environment, EnvKind, FileRole, Format, Span

#: A marker that names a step of an argument (`\\textbf{Step 1: …}`).
STEP_RE = re.compile(r"^(?:Step|Stage|Phase|Case)\s+\S+", re.IGNORECASE)
#: Displays whose rows `\\\\` separates, each row its own equation.
ROWED_DISPLAYS: frozenset[str] = frozenset(
    {"align", "alignat", "gather", "flalign", "eqnarray"}
)
_ROW_BREAK_RE = re.compile(r"\\\\(?:\*)?(?:\s*\[[^\]]*\])?")
#: What may stand before a run-in heading that opens a block: `\\noindent`,
#: and the `\\begin{…}[…]` a transparent environment lends its first block.
_OPENER_RE = re.compile(r"\s*(?:\\noindent(?![a-zA-Z@])|\\begin\{[a-zA-Z*]+\})\s*")
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)


def is_step(part: MarkedPart) -> bool:
    return (
        part.kind is PartKind.HEADED
        and part.marker is not None
        and STEP_RE.match(part.marker) is not None
    )


def marked_parts(document: Document, index: BlockIndex) -> tuple[MarkedPart, ...]:
    """Every marked part of the paper, in the order its blocks stand, each
    block's in the order of the text."""
    found: list[MarkedPart] = []
    viewed = views(document)
    found.extend(_latex_items(document, viewed, index))
    found.extend(_markdown_items(document, viewed, index))
    found.extend(_labelled_displays(document, viewed, index))
    found.extend(_headed(document, viewed, index))
    found = [part for part in found if part.block in index.position]
    found.sort(key=lambda part: (index.position[part.block], part.at.start, part.kind))
    return tuple(found)


def _latex_items(
    document: Document, viewed: dict[str, FileView], index: BlockIndex
) -> list[MarkedPart]:
    found: list[MarkedPart] = []
    for environment in document.environments:
        if environment.kind is not EnvKind.LIST:
            continue
        view = viewed[environment.span.file]
        body = environment.body
        inner = [
            other
            for other in document.environments
            if other.span.file == body.file
            and other is not environment
            and body.start <= other.span.start
            and other.span.end <= body.end
        ]
        skip = [(other.span.start, other.span.end) for other in inner]
        skip += [
            (note.at.start, note.at.end)
            for note in document.footnotes
            if note.at.file == body.file
        ]
        items = latex_items(view, body.start, body.end, skip)
        for ordinal, item in enumerate(items, start=1):
            end = items[ordinal].start if ordinal < len(items) else body.end
            nested = [
                (other.span.start, other.span.end)
                for other in inner
                if other.kind is EnvKind.LIST and item.start <= other.span.start < end
            ]
            owner = index.owner(view.path, item.start)
            if owner is None:
                continue
            found.append(
                MarkedPart(
                    block=owner,
                    kind=PartKind.ITEM,
                    marker=item.marker,
                    ordinal=ordinal,
                    label=_label_in(document, view.path, item.start, end, nested),
                    at=view.span(*view.trim(item.start, _within(index, owner, end))),
                )
            )
    return found


def _markdown_items(
    document: Document, viewed: dict[str, FileView], index: BlockIndex
) -> list[MarkedPart]:
    found: list[MarkedPart] = []
    for source in document.files:
        view = viewed[source.path]
        if view.format is not Format.MARKDOWN:
            continue
        if source.role in (FileRole.PREAMBLE, FileRole.PACKAGE):
            continue
        skip = [
            (environment.span.start, environment.span.end)
            for environment in document.environments
            if environment.span.file == source.path
            and environment.kind in (EnvKind.DISPLAY, EnvKind.VERBATIM)
        ]
        # A stack of [indent, marker type, count]: a new marker type at the
        # same indent starts a new list, as in CommonMark.
        lists: list[list] = []
        previous_end: int | None = None
        lines = len(view.starts) - 1
        for line in range(lines):
            item = markdown_item_at(view, line)
            if item is None or any(start <= item.start < end for start, end in skip):
                continue
            if previous_end is not None and _list_ended(view, previous_end, line):
                lists = []
            while lists and lists[-1][0] > item.indent:
                lists.pop()
            kind = item.marker[-1] if item.marker[0].isdigit() else item.marker
            if lists and lists[-1][0] == item.indent and lists[-1][1] == kind:
                lists[-1][2] += 1
            else:
                if lists and lists[-1][0] == item.indent:
                    lists.pop()
                lists.append([item.indent, kind, 1])
            end_line = _item_last_line(view, line, lines)
            end = view.line_end(end_line)
            previous_end = end_line
            owner = index.owner(view.path, item.start)
            if owner is None:
                continue
            found.append(
                MarkedPart(
                    block=owner,
                    kind=PartKind.ITEM,
                    marker=item.marker,
                    ordinal=lists[-1][2],
                    label=_label_in(document, view.path, item.start, end, ()),
                    at=view.span(*view.trim(item.start, _within(index, owner, end))),
                )
            )
    return found


def _within(index: BlockIndex, block: str, end: int) -> int:
    """`end`, held to the end of `block`: a part lies inside its block, so
    an item a statement cuts is marked up to it."""
    return min(end, index.by_id[block].extent.end)


def _continues(view: FileView, line: int) -> bool:
    """Whether a non-blank line after a blank one continues a list item."""
    return (
        markdown_item_at(view, line) is not None
        or indent_of(view, line) >= CONTINUATION_INDENT
    )


def _item_last_line(view: FileView, line: int, lines: int) -> int:
    """The last line of the Markdown item opened at `line`."""
    last = line
    following = line + 1
    blank = False
    while following < lines:
        if view.blank[following]:
            blank = True
        elif markdown_item_at(view, following) is not None or (
            blank and indent_of(view, following) < CONTINUATION_INDENT
        ):
            break
        else:
            last = following
            blank = False
        following += 1
    return last


def _list_ended(view: FileView, previous_last: int, line: int) -> bool:
    """Whether anything but the list stands between its last item and the
    item at `line`."""
    for between in range(previous_last + 1, line):
        if not view.blank[between] and not _continues(view, between):
            return True
    return False


def _label_in(
    document: Document,
    path: str,
    start: int,
    end: int,
    excluded: Sequence[tuple[int, int]],
) -> str | None:
    for label in document.labels:
        at = label.at
        if at.file != path or not start <= at.start < end:
            continue
        if any(first <= at.start < last for first, last in excluded):
            continue
        return label.name
    return None


def _labelled_displays(
    document: Document, viewed: dict[str, FileView], index: BlockIndex
) -> list[MarkedPart]:
    found: list[MarkedPart] = []
    displays = [
        environment
        for environment in document.environments
        if environment.kind is EnvKind.DISPLAY
    ]
    labelled = {
        display.span
        for display in displays
        for label in document.labels
        if _holds(display.span, label.at)
    }
    for label in document.labels:
        view = viewed[label.at.file]
        around = [display for display in displays if _holds(display.span, label.at)]
        if around:
            display = max(around, key=lambda env: env.span.start)
            at = display.span
            if display.name.rstrip("*") in ROWED_DISPLAYS:
                at = _row(view, display.body, label.at.start)
            holder = index.holder(label.at)
        else:
            display = _display_after(view, displays, label.at)
            if display is None or display.span in labelled:
                continue
            at = display.span
            holder = index.holder(at)
        if holder is None:
            continue
        found.append(
            MarkedPart(
                block=holder,
                kind=PartKind.LABELLED_DISPLAY,
                marker=label.name,
                ordinal=None,
                label=label.name,
                at=at,
            )
        )
    return found


def _holds(outer: Span, inner: Span) -> bool:
    return (
        outer.file == inner.file
        and outer.start <= inner.start
        and inner.end <= outer.end
    )


def _display_after(
    view: FileView, displays: Sequence[Environment], label: Span
) -> Environment | None:
    """The display a label written just before it names: the next display in
    its file, with nothing rendered between (a sentence's last word, then
    `\\label{…}`, then its `$$…$$`)."""
    following = [
        display
        for display in displays
        if display.span.file == label.file and display.span.start >= label.end
    ]
    if not following:
        return None
    display = min(following, key=lambda env: env.span.start)
    if view.renders(label.end, display.span.start):
        return None
    return display


def _row(view: FileView, body: Span, offset: int) -> Span:
    """The row of a rowed display that holds `offset`, between `\\\\`s."""
    start = body.start
    end = body.end
    for match in _ROW_BREAK_RE.finditer(view.rendered, body.start, body.end):
        if match.end() <= offset:
            start = match.end()
        elif match.start() >= offset:
            end = match.start()
            break
    return view.span(*view.trim(start, end))


def _headed(
    document: Document, viewed: dict[str, FileView], index: BlockIndex
) -> list[MarkedPart]:
    found: list[MarkedPart] = []
    seen: set[tuple[str, int]] = set()
    for source in document.files:
        if source.role in (FileRole.PREAMBLE, FileRole.PACKAGE):
            continue
        view = viewed[source.path]
        for match in PARAGRAPH_RE.finditer(view.rendered):
            closing = braced_end(view.rendered, match.end())
            if closing is None:
                continue
            owner = index.owner(view.path, match.start())
            if owner is None:
                continue
            seen.add((view.path, match.start()))
            found.append(
                _headed_part(
                    owner, view, match.start(), closing, match.end() + 1, closing - 1
                )
            )
    for environment in document.environments:
        if environment.kind is not EnvKind.LIST:
            continue
        view = viewed[environment.span.file]
        body = environment.body
        skip = [
            (other.span.start, other.span.end)
            for other in document.environments
            if other.span.file == body.file
            and other is not environment
            and body.start <= other.span.start
            and other.span.end <= body.end
        ]
        for item in latex_items(view, body.start, body.end, skip):
            owner = index.owner(view.path, item.start)
            if owner is None:
                continue
            run = _run_in(view, item.head_end, index.by_id[owner].extent.end)
            if run is None or (view.path, run.start) in seen:
                continue
            seen.add((view.path, run.start))
            found.append(
                _headed_part(
                    owner, view, run.start, run.end, run.inner_start, run.inner_end
                )
            )
    for block in index.blocks:
        if block.form in (Form.TITLE, Form.HEADING):
            # A title or a heading is a heading already (a `{\\bf …}`
            # title).
            continue
        part = _bold_opening(viewed[block.extent.file], block)
        if part is not None and (block.extent.file, part.at.start) not in seen:
            found.append(part)
    return found


def _headed_part(
    block: str, view: FileView, start: int, end: int, inner: int, inner_end: int
) -> MarkedPart:
    return MarkedPart(
        block=block,
        kind=PartKind.HEADED,
        marker=plain(view.rendered[inner:inner_end]),
        ordinal=None,
        label=None,
        at=view.span(start, end),
    )


def _bold_opening(view: FileView, block: Block) -> MarkedPart | None:
    """A run-in heading that opens a block, after `\\noindent` or the
    opening a transparent environment lent it."""
    start = block.owned[0].start
    text = view.rendered
    while True:
        match = _OPENER_RE.match(text, start)
        if match is None or match.end() == start:
            break
        start = match.end()
        closing = bracketed_end(text, start)
        if closing is not None:
            start = closing
    run = _run_in(view, start, block.extent.end)
    if run is not None:
        return _headed_part(
            block.id, view, run.start, run.end, run.inner_start, run.inner_end
        )
    while start < len(text) and text[start].isspace():
        start += 1
    markdown = _MARKDOWN_BOLD_RE.match(text, start)
    if markdown is not None and markdown.end() <= block.extent.end:
        return _headed_part(
            block.id, view, start, markdown.end(), start + 2, markdown.end() - 2
        )
    return None


def _run_in(view: FileView, start: int, limit: int) -> RunIn | None:
    """A LaTeX run-in heading at `start`, past whitespace, ending by
    `limit`: a bold run, or an italic one with text after it before
    `limit`."""
    text = view.rendered
    while start < limit and text[start].isspace():
        start += 1
    run = bold_run(text, start)
    if run is None:
        run = italic_run_in(text, start)
        if run is not None and not view.renders(run.end, limit):
            return None
    if run is None or run.end > limit:
        return None
    return run
