"""Text arithmetic the cutter's rules share.

A `FileView` is one file as the rules read it: its source, what it renders
(hidden text blanked, newlines kept, so every offset is the file's own), and
which of its lines are blank. The item scanners find `\\item`s and Markdown
list items, which the document layer does not record; the block rules cut at
them and the marked parts list them.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Sequence
from dataclasses import dataclass

from axiomation.reader import Document, Format, SourceFile, Span

ITEM_RE = re.compile(r"\\item(?![a-zA-Z@])")
#: A Markdown list item's marker at the start of a line: `-`, `*`, `+`, or a
#: number with `.` or `)`, then a space and text.
MARKDOWN_ITEM_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>[-*+]|\d{1,9}[.)])[ \t]+\S"
)
#: `\\paragraph{…}` and `\\subparagraph{…}`: LaTeX sets them run-in, so they
#: open the paragraph that follows.
PARAGRAPH_RE = re.compile(r"\\(?:sub)?paragraph\*?\s*(?=\{)")
_COMMAND_RE = re.compile(r"\\(?:[a-zA-Z@]+\*?|.)")
#: `\\textbf{…}`, and the declaration forms `{\\bf …}` and `{\\bfseries …}`.
_BOLD_COMMAND_RE = re.compile(r"\\textbf\s*(?=\{)")
_BOLD_GROUP_RE = re.compile(r"\{\s*\\(?:bf|bfseries)(?![a-zA-Z@])\s*")
NOINDENT_RE = re.compile(r"\\noindent(?![a-zA-Z@])\s*")
_ITALIC_COMMAND_RE = re.compile(r"\\(?:emph|textit)\s*(?=\{)")
_ITALIC_GROUP_RE = re.compile(r"\{\s*\\(?:em|it|itshape)(?![a-zA-Z@])\s*")
#: How a run-in heading ends: LaTeX's own run-in headings end in a full stop,
#: and papers write a colon, some in parentheses, `(… .)`.
_RUN_IN_END_RE = re.compile(r"[.:]\)?\s*$")
#: The most words an italic run-in heading holds: a step's title runs to
#: seven; an emphasised sentence is longer as a rule.
RUN_IN_WORDS = 8


@dataclass(frozen=True, slots=True)
class FileView:
    path: str
    format: Format
    text: str
    #: `text` with every hidden character blanked; newlines kept.
    rendered: str
    #: 1 where the character is hidden.
    hidden: bytes
    #: The offset each line starts at, and one past the end.
    starts: tuple[int, ...]
    #: Whether each line is whitespace in the source.
    blank: tuple[bool, ...]

    @classmethod
    def of(cls, source: SourceFile) -> FileView:
        text = source.text
        hidden = bytearray(len(text))
        chars = list(text)
        for run in source.hidden:
            hidden[run.span.start : run.span.end] = b"\x01" * (
                run.span.end - run.span.start
            )
            for index in range(run.span.start, run.span.end):
                if chars[index] != "\n":
                    chars[index] = " "
        lines = text.split("\n")
        starts = [0]
        for line in lines:
            starts.append(starts[-1] + len(line) + 1)
        return cls(
            path=source.path,
            format=source.format,
            text=text,
            rendered="".join(chars),
            hidden=bytes(hidden),
            starts=tuple(starts),
            blank=tuple(not line.strip() for line in lines),
        )

    def line_of(self, offset: int) -> int:
        """The 0-based line an offset stands on."""
        return bisect.bisect_right(self.starts, offset) - 1

    def line_end(self, line: int) -> int:
        """The offset of the newline ending `line`, or the text's end."""
        return min(self.starts[line + 1] - 1, len(self.text))

    def renders(self, start: int, end: int) -> bool:
        """Whether `text[start:end]` holds a rendered character that is not
        whitespace."""
        return bool(self.rendered[start:end].strip())

    def trim(self, start: int, end: int) -> tuple[int, int]:
        """`start`, `end` moved inward past source whitespace."""
        while start < end and self.text[start].isspace():
            start += 1
        while end > start and self.text[end - 1].isspace():
            end -= 1
        return start, end

    def runs(
        self, start: int, end: int, holes: Sequence[tuple[int, int]] = ()
    ) -> list[tuple[int, int]]:
        """The rendered runs of `start`..`end` outside `holes`, each trimmed
        of whitespace; a run of whitespace alone is dropped."""
        excluded = bytearray(self.hidden[start:end])
        for hole_start, hole_end in holes:
            first, last = max(hole_start, start), min(hole_end, end)
            if last > first:
                excluded[first - start : last - start] = b"\x01" * (last - first)
        found: list[tuple[int, int]] = []
        index = 0
        length = end - start
        while index < length:
            if excluded[index]:
                index += 1
                continue
            run = index
            while index < length and not excluded[index]:
                index += 1
            first, last = self.trim(start + run, start + index)
            if last > first:
                found.append((first, last))
        return found

    def span(self, start: int, end: int) -> Span:
        return Span(self.path, start, end)


def views(document: Document) -> dict[str, FileView]:
    return {source.path: FileView.of(source) for source in document.files}


def bracketed_end(text: str, start: int) -> int | None:
    """The index just past a `[…]` opening at `text[start]`, braces nested."""
    if start >= len(text) or text[start] != "[":
        return None
    depth = 0
    for index in range(start + 1, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "]" and depth <= 0:
            return index + 1
    return None


def braced_end(text: str, start: int) -> int | None:
    """The index just past the `}` closing a group opening at `text[start]`."""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _inside(offset: int, ranges: Sequence[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


@dataclass(frozen=True, slots=True)
class LatexItem:
    """One `\\item`: where it stands, and its `[…]` marker if written."""

    start: int
    marker: str | None
    #: Just past the command and its marker.
    head_end: int


def latex_items(
    view: FileView, start: int, end: int, skip: Sequence[tuple[int, int]]
) -> list[LatexItem]:
    """The `\\item`s of `start`..`end` in rendered text, outside `skip`."""
    found: list[LatexItem] = []
    for match in ITEM_RE.finditer(view.rendered, start, end):
        if _inside(match.start(), skip):
            continue
        marker = None
        head_end = match.end()
        after = match.end()
        while after < end and view.rendered[after] in " \t":
            after += 1
        closing = bracketed_end(view.rendered, after)
        if closing is not None and closing <= end:
            marker = view.rendered[after + 1 : closing - 1].strip()
            head_end = closing
        found.append(LatexItem(match.start(), marker, head_end))
    return found


@dataclass(frozen=True, slots=True)
class MarkdownItem:
    """One Markdown list item's first line: where its line starts, its
    indent, and its marker as written."""

    line_start: int
    start: int
    indent: int
    marker: str


def markdown_item_at(view: FileView, line: int) -> MarkdownItem | None:
    """The Markdown list item a line opens, if it opens one.

    As in CommonMark, an ordered item that does not start at 1 cannot
    interrupt a paragraph: a wrapped line that begins `2. ` is text, unless
    the paragraph it continues is itself a list item's.
    """
    item = _marker_at(view, line)
    if item is None or not item.marker[0].isdigit() or int(item.marker[:-1]) == 1:
        return item
    above = line - 1
    while above >= 0 and not view.blank[above]:
        if _marker_at(view, above) is not None:
            return item
        above -= 1
    return item if above == line - 1 else None


def _marker_at(view: FileView, line: int) -> MarkdownItem | None:
    """A list marker at the start of a line, whatever stands above it."""
    if view.format is not Format.MARKDOWN:
        return None
    start = view.starts[line]
    text = view.rendered[start : view.line_end(line)]
    match = MARKDOWN_ITEM_RE.match(text)
    if match is None:
        return None
    indent = len(match.group("indent").expandtabs(4))
    return MarkdownItem(start, start + match.start("marker"), indent, match["marker"])


def markdown_items(
    view: FileView, start: int, end: int, skip: Sequence[tuple[int, int]]
) -> list[MarkdownItem]:
    """The Markdown list items whose marker stands in `start`..`end`,
    outside `skip`."""
    found: list[MarkdownItem] = []
    if view.format is not Format.MARKDOWN:
        return found
    for line in range(view.line_of(start), view.line_of(max(end - 1, start)) + 1):
        item = markdown_item_at(view, line)
        if (
            item is not None
            and start <= item.start < end
            and not _inside(item.start, skip)
        ):
            found.append(item)
    return found


def indent_of(view: FileView, line: int) -> int:
    text = view.text[view.starts[line] : view.line_end(line)]
    return len(text.expandtabs(4)) - len(text.expandtabs(4).lstrip())


@dataclass(frozen=True, slots=True)
class RunIn:
    """A run that sets a heading in the paragraph it opens: where it stands,
    and where its text stands inside it."""

    start: int
    end: int
    inner_start: int
    inner_end: int


def bold_run(text: str, start: int) -> RunIn | None:
    """A bold run at `text[start]`: `\\textbf{…}`, `{\\bf …}` or
    `{\\bfseries …}`."""
    return _run(text, start, _BOLD_COMMAND_RE, _BOLD_GROUP_RE)


def italic_run_in(text: str, start: int) -> RunIn | None:
    """An italic run at `text[start]` that is short and ends in `.`, `:` or `.)`,
    as a run-in heading does: `\\emph{…}`, `\\textit{…}`, `{\\em …}`,
    `{\\it …}` or `{\\itshape …}` (`\\item {\\em Step 1 (…):}`).
    Italics also emphasise a sentence, so the caller asks as well
    that text follow it."""
    run = _run(text, start, _ITALIC_COMMAND_RE, _ITALIC_GROUP_RE)
    if run is None:
        return None
    words = plain(text[run.inner_start : run.inner_end])
    if _RUN_IN_END_RE.search(words) is None or len(words.split()) > RUN_IN_WORDS:
        return None
    return run


def _run(
    text: str, start: int, command_re: re.Pattern[str], group_re: re.Pattern[str]
) -> RunIn | None:
    command = command_re.match(text, start)
    if command is not None:
        end = braced_end(text, command.end())
        if end is None:
            return None
        return RunIn(start, end, command.end() + 1, end - 1)
    group = group_re.match(text, start)
    if group is not None:
        end = braced_end(text, start)
        if end is None:
            return None
        return RunIn(start, end, group.end(), end - 1)
    return None


def plain(text: str) -> str:
    """Text with its commands and braces dropped, whitespace collapsed:
    `\\textbf{Step 1:} \\emph{x}` is `Step 1: x`."""
    text = _COMMAND_RE.sub(" ", text)
    text = text.replace("{", "").replace("}", "").replace("~", " ")
    return " ".join(text.split())
