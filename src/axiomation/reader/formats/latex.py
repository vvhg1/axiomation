"""Plain LaTeX: the second entry in the format registry.

Environments,
`\\label`, `\\ref`, `\\cite`, proofs and mathematics are the same syntax in a
`.tex` file as in Markdown+LaTeX, so this class inherits them and overrides
what differs:

- **Headings** are `\\part` to `\\subsubsection`, starred or not, with an
  optional short title. Their levels only order them.
- **What is not read**: the preamble through `\\begin{document}`, everything
  from `\\end{document}` on, `\\iffalse` blocks and `verbatim`-like bodies.
  A comment is blanked, length kept, before any syntax pass reads the line,
  so a commented-out environment is no environment and a `\\ref` in a
  comment is no reference.
- **Directives** are `%` comments (the second spelling), whole-line or
  trailing. `<!-- @meta -->` in a `.tex` file is text LaTeX prints, and it
  is not read: each format reads its own spelling.
- **Markup lines** also include the commands that only assemble a document:
  `\\input`, `\\maketitle`, `\\appendix`, `\\printbibliography` and their kin.

`read_mask` says which characters are a comment, an `\\iffalse` block or a
verbatim body; the line functions read from it.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from axiomation.reader.directives import Directive, parse_directives
from axiomation.reader.formats.md_latex import (
    LABEL_RE,
    MdLatexFormat,
    comment_start,
    uncommented,
)

#: The sectioning commands, outermost first. The position is the level.
#: `\\paragraph` and `\\subparagraph` are not here: LaTeX sets them as bold
#: run-in text inside a paragraph, unnumbered by default, and nobody labels or
#: references one, so a `\\paragraph{Notation.}` is text of its section.
SECTIONING: tuple[str, ...] = (
    "part",
    "chapter",
    "section",
    "subsection",
    "subsubsection",
)
HEADING_RE = re.compile(
    r"^\s*\\(" + "|".join(SECTIONING) + r")(\*?)\s*(?:\[[^\]]*\])?\s*\{"
)

#: A directive inside a `%` comment, to the end of the line. It is matched
#: only where `comment_start` says a comment begins, so the two can never
#: disagree about which `%` opens one.
TEX_DIRECTIVE_RE = re.compile(r"%[ \t]*@(?P<name>[a-z_]+)(?P<body>.*)$")

BEGIN_DOCUMENT_RE = re.compile(r"\\begin\{document\}")
END_DOCUMENT_RE = re.compile(r"\\end\{document\}")
#: Environments whose body is not LaTeX to be read: code, and the `comment`
#: package's block comment.
VERBATIM_ENVS: tuple[str, ...] = (
    "verbatim",
    "verbatim*",
    "lstlisting",
    "minted",
    "comment",
)
#: The one of them that is a comment, not a listing.
COMMENT_ENV = "comment"
_VERBATIM_BEGIN_RE = re.compile(
    r"\\begin\{(" + "|".join(re.escape(env) for env in VERBATIM_ENVS) + r")\}"
)

#: Commands that assemble a document and say nothing in it. A line holding
#: only these (and labels) is structure, like a `\label`-only line.
STRUCTURE_RE = re.compile(
    r"\\(?:input|include|subfile)\{[^}]*\}"
    r"|\\(?:sub)?import\*?\{[^}]*\}\{[^}]*\}"
    r"|\\bibliography(?:style)?\{[^}]*\}"
    r"|\\(?:maketitle|tableofcontents|appendix|printbibliography|newpage"
    r"|clearpage|frontmatter|mainmatter|backmatter)\b(?:\[[^\]]*\])?"
    r"|\\(?:else|fi)(?![a-zA-Z@])"
)


#: How many lines a wrapped heading title is read over.
TITLE_LINES = 4
#: A TeX conditional's parts: any `\\if...` opens one, `\\else` and `\\fi`.
_CONDITIONAL_RE = re.compile(r"\\(if[a-zA-Z@]*|else|fi)(?![a-zA-Z@])")
#: A definition: the command and the name it defines.
_DEFINITION_RE = re.compile(
    r"\\(?P<how>[gex]?def|let|newif|newcommand|renewcommand|providecommand"
    r"|DeclareRobustCommand)(?![a-zA-Z@])\*?\s*"
    r"(?:\{\s*\\[A-Za-z@]+\s*\}|\\[A-Za-z@]+)"
)
#: What follows the name, up to the body's brace or to the end of a `\\let`:
#: a `\\def`'s parameter text on its line, its body's brace there or at
#: the next line's start; a `\\newcommand`'s argument
#: count and default; a `\\let`'s right side, one token.
_DEFINITION_TAILS = {
    "def": re.compile(r"[^{}\n]*?(?:\n[ \t]*)?(?=\{)"),
    "command": re.compile(r"(?:\s*\[\s*\d\s*\])?(?:\s*\[[^\]\n]*\])?\s*(?=\{)"),
    "let": re.compile(r"\s*=?\s*(?:\\[A-Za-z@]+|\\.|\S)"),
}


def braced_end(text: str, start: int) -> int | None:
    """The index just past the `}` closing the group opening at `text[start]`.

    Nesting is kept, and an escaped brace is text.
    """
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "\\":
            continue
        if char == "{" and (index == 0 or text[index - 1] != "\\"):
            depth += 1
        elif char == "}" and text[index - 1] != "\\":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _braced(text: str, start: int) -> str | None:
    """The contents of the `{...}` group opening at `text[start]`, nesting kept."""
    end = braced_end(text, start)
    return None if end is None else text[start + 1 : end - 1]


@dataclass(frozen=True, slots=True)
class Disabled:
    """An `\\iffalse` block: from the `\\iffalse` to the end of the `\\else` or
    `\\fi` that ends it, as offsets into the text."""

    start: int
    end: int


def definitions(text: str) -> list[tuple[int, int]]:
    """Where `text` defines a command, as `(start, end)` offsets: the
    command, the name it defines, and its body or a `\\let`'s right side.

    A conditional written there is not one where it stands: it is what the
    defined command does when used, and the name a `\\newif` or a `\\let`
    defines is no conditional opened. Nested definitions fall inside the
    outer one's span. A body whose brace never closes ends at its brace.
    """
    found: list[tuple[int, int]] = []
    for match in _DEFINITION_RE.finditer(text):
        if found and match.start() < found[-1][1]:
            continue
        how, end = match.group("how"), match.end()
        kind = "let" if how == "let" else "def" if how.endswith("def") else "command"
        tail = None if how == "newif" else _DEFINITION_TAILS[kind].match(text, end)
        if tail is not None:
            end = tail.end()
            if kind != "let":
                end = braced_end(text, end) or end
        found.append((match.start(), end))
    return found


def conditionally_false(text: str) -> list[Disabled]:
    """Every `\\iffalse` block, up to its `\\else` or `\\fi`.

    `\\iffalse ... \\fi` is how an author comments out a block, so what is
    between is not the paper; after an `\\else` at the same depth it is again.
    Nested conditionals are counted so an inner `\\fi` does not end the block.
    A conditional inside a definition (`definitions`) opens and closes
    nothing: `\\def\\ifdraft{\\iffalse}` hides no text after it.
    `text` has its comments blanked. The answer is in offsets.
    """
    found: list[Disabled] = []
    opened: int | None = None
    depth = 0
    spans = definitions(text)
    starts = [start for start, _ in spans]
    for token in _CONDITIONAL_RE.finditer(text):
        at = bisect_right(starts, token.start()) - 1
        if at >= 0 and token.start() < spans[at][1]:
            continue
        word = token.group(1)
        if opened is None:
            if word == "iffalse":
                opened, depth = token.start(), 0
            continue
        if word.startswith("if"):
            depth += 1
        elif depth == 0:
            found.append(Disabled(opened, token.end()))
            opened = None
        elif word == "fi":
            depth -= 1
    if opened is not None:
        found.append(Disabled(opened, len(text)))
    return found


@dataclass(frozen=True, slots=True)
class Verbatim:
    """A verbatim-like environment, as offsets: its `\\begin`, where its body
    starts and ends, and the end of its `\\end` (the end of the text when
    nothing closes it)."""

    name: str
    start: int
    body_start: int
    body_end: int
    end: int
    closed: bool


@dataclass(frozen=True, slots=True)
class Mask:
    """What of a LaTeX file no syntax pass reads, wherever it sits.

    `visible` is the text with every comment, `\\iffalse` block and verbatim
    body blanked, newlines and lengths kept. `comments` are `(start, end)`
    offsets of each `%` comment, to the end of its line.
    """

    visible: str
    comments: tuple[tuple[int, int], ...]
    verbatims: tuple[Verbatim, ...]
    disabled: tuple[Disabled, ...]


def read_mask(text: str) -> Mask:
    """Comments, verbatim bodies and `\\iffalse` blocks, in one pass.

    A line is read in order: inside a verbatim body nothing is a comment
    until its `\\end{…}`; outside one, a `%` starts a comment and a
    `\\begin{verbatim}` before it opens a body.
    """
    comments: list[tuple[int, int]] = []
    verbatims: list[Verbatim] = []
    chars = list(text)
    offset = 0
    open_verbatim: tuple[str, int, int] | None = None
    for line in text.split("\n"):
        column = 0
        while column <= len(line):
            if open_verbatim is not None:
                name, start, body_start = open_verbatim
                closing = "\\end{" + name + "}"
                found = line.find(closing, column)
                if found < 0:
                    break
                verbatims.append(
                    Verbatim(
                        name,
                        start,
                        body_start,
                        offset + found,
                        offset + found + len(closing),
                        True,
                    )
                )
                open_verbatim = None
                column = found + len(closing)
                continue
            rest = line[column:]
            comment = comment_start(rest)
            limit = len(rest) if comment is None else comment
            opened = _VERBATIM_BEGIN_RE.search(rest, 0, limit)
            if opened is not None:
                open_verbatim = (
                    opened.group(1),
                    offset + column + opened.start(),
                    offset + column + opened.end(),
                )
                column += opened.end()
                continue
            if comment is not None:
                comments.append((offset + column + comment, offset + len(line)))
            break
        offset += len(line) + 1
    if open_verbatim is not None:
        name, start, body_start = open_verbatim
        verbatims.append(Verbatim(name, start, body_start, len(text), len(text), False))
    for start, end in comments:
        chars[start:end] = " " * (end - start)
    for verbatim in verbatims:
        if verbatim.name == COMMENT_ENV:
            _blank(chars, verbatim.start, verbatim.end)
        else:
            _blank(chars, verbatim.body_start, verbatim.body_end)
    disabled = conditionally_false("".join(chars))
    for block in disabled:
        _blank(chars, block.start, block.end)
    return Mask("".join(chars), tuple(comments), tuple(verbatims), tuple(disabled))


def _blank(chars: list[str], start: int, end: int) -> None:
    """Spaces over `chars[start:end]`, newlines kept."""
    for index in range(start, end):
        if chars[index] != "\n":
            chars[index] = " "


def unread_blocks(lines: Sequence[str]) -> list[bool]:
    """The lines no pass reads wherever they sit: `\\iffalse` and verbatim.

    A line is unread when an `\\iffalse` block or a verbatim environment,
    its `\\begin` and `\\end` included, touches it.
    The preamble is fenced for the text passes and still declares the
    paper's environments, so the declaration reader needs this half of the
    fence alone.
    """
    text = "\n".join(lines)
    mask = read_mask(text)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    fence = [False] * len(lines)
    ranges = [(block.start, block.end) for block in mask.disabled]
    ranges += [(verbatim.start, verbatim.end) for verbatim in mask.verbatims]
    for start, end in ranges:
        for number in range(len(lines)):
            line_start, line_end = starts[number], starts[number + 1] - 1
            if line_start <= max(end - 1, start) and start <= line_end:
                fence[number] = True
    return fence


class LatexFormat(MdLatexFormat):
    """Line mechanics for one plain-LaTeX file."""

    name = "latex"

    @staticmethod
    def directives(lines: Sequence[str], fence_map: Sequence[bool]) -> list[Directive]:
        """Every directive in a comment outside a fence, columns kept."""
        comments = []
        for line in lines:
            start = comment_start(line)
            comments.append("" if start is None else " " * start + line[start:])
        return [
            found
            for found in parse_directives(comments, TEX_DIRECTIVE_RE)
            if not fence_map[found.line - 1]
        ]

    @staticmethod
    def readable(lines: Sequence[str]) -> list[str]:
        """Every line with its comment blanked, so no pass reads a comment."""
        out: list[str] = []
        for line in lines:
            kept = uncommented(line)
            out.append(kept + " " * (len(line) - len(kept)))
        return out

    @staticmethod
    def unmarked(line: str) -> str:
        """Nothing to blank: `readable` already removed every comment, and a
        directive is one."""
        return line

    @staticmethod
    def heading_level(line: str) -> int | None:
        match = HEADING_RE.match(line)
        return SECTIONING.index(match.group(1)) + 1 if match else None

    @staticmethod
    def heading_title(line: str) -> str:
        """The braced title with any `\\label{}` removed."""
        match = HEADING_RE.match(line)
        if not match:
            return ""
        title = _braced(line, match.end() - 1)
        if title is None:
            return ""
        return LABEL_RE.sub("", title).strip()

    @classmethod
    def heading_title_at(cls, lines: Sequence[str], index: int) -> str:
        """The title of the heading at `lines[index]`, wrapped lines included.

        A long title is often broken across lines inside its braces; the
        group is read on until it closes, over a few lines at most.
        """
        match = HEADING_RE.match(lines[index])
        if not match:
            return ""
        text = lines[index]
        for following in lines[index + 1 : index + TITLE_LINES]:
            if _braced(text, match.end() - 1) is not None:
                break
            text += " " + following.strip()
        title = _braced(text, match.end() - 1)
        return " ".join(LABEL_RE.sub("", title).split()) if title is not None else ""

    @classmethod
    def heading_end(cls, lines: Sequence[str], index: int) -> tuple[int, int]:
        """Where the heading at `lines[index]` ends: `(line, column)` just past
        the brace closing its title, read over a few lines at most. A title
        that never closes ends with its line."""
        match = HEADING_RE.match(lines[index])
        if not match:
            return (index, len(lines[index]))
        window = lines[index : index + TITLE_LINES]
        text = "\n".join(window)
        end = braced_end(text, match.end() - 1)
        if end is None:
            return (index, len(lines[index]))
        line = text.count("\n", 0, end)
        column = end - (text.rfind("\n", 0, end) + 1)
        return (index + line, column)

    @staticmethod
    def heading_attributes(line: str) -> tuple[int, int, str | None, bool] | None:
        """LaTeX has no pandoc attributes."""
        return None

    @staticmethod
    def heading_starred(line: str) -> bool:
        match = HEADING_RE.match(line)
        return bool(match and match.group(2))

    @staticmethod
    def build_fence_map(lines: list[str]) -> list[bool]:
        """The preamble, the end matter and every verbatim block.

        A file with no `\\begin{document}` is body from its first line: a
        section file `\\input` by the root carries none.
        """
        fence = [False] * len(lines)
        visible = [uncommented(line) for line in lines]
        begin = next(
            (n for n, line in enumerate(visible) if BEGIN_DOCUMENT_RE.search(line)),
            None,
        )
        if begin is not None:
            for number in range(begin + 1):
                fence[number] = True
        end = next(
            (n for n, line in enumerate(visible) if END_DOCUMENT_RE.search(line)),
            None,
        )
        if end is not None:
            for number in range(end, len(lines)):
                fence[number] = True
        for number, inert in enumerate(unread_blocks(lines)):
            fence[number] = fence[number] or inert
        return fence

    @staticmethod
    def is_markup(line: str) -> bool:
        """A line of labels, directives and document assembly, and no text."""
        bare = STRUCTURE_RE.sub("", LABEL_RE.sub("", uncommented(line)))
        return not bare.strip()
