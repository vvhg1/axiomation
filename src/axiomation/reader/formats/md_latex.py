"""Markdown with embedded LaTeX: the line mechanics every other module reads.

Everything here is
text arithmetic over a list of lines: what is a heading, what is inside a
code fence, where an environment ends. It knows nothing about blocks, roles
or edges.

Every function that answers with a line gives the column as well, so
spans are character-precise.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from axiomation.reader.directives import (
    DIRECTIVE_RE,
    Directive,
    parse_directives,
    strip_directives,
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
STRIP_RE = re.compile(r"\\(begin|end|label)\{[^}]*\}")
#: pandoc's attributes at the end of a heading line: `{#appendix .unnumbered}`.
#: The id is the heading's label; `.unnumbered` and `-` unnumber it.
HEADING_ATTRIBUTES_RE = re.compile(r"\s*\{(?=\s*[#.-])(?P<body>[^{}]*)\}\s*$")
_ATTRIBUTE_ID_RE = re.compile(r"(?:^|\s)#(?P<id>[^\s}]+)")
_UNNUMBERED_RE = re.compile(r"(?:^|\s)(?:\.unnumbered|-)(?=\s|$)")

#: Environments that carry a statement. This list only says where a
#: statement's text starts and stops.
STATEMENT_ENVS: tuple[str, ...] = (
    "theorem",
    "proposition",
    "lemma",
    "corollary",
    "conjecture",
    "definition",
    "remark",
    "assumption",
    "condition",
    "fact",
)

#: Environments a trailing `\begin{proof}` belongs to. A definition or a
#: standing assumption is not proved, so a proof after one is standalone.
PROVABLE_ENVS: frozenset[str] = frozenset(
    {"theorem", "proposition", "lemma", "corollary", "fact"}
)

#: thmtools' environment that prints a statement and declares a command
#: that prints it again: `\\begin{restatable}[Title]{theorem}{main}`. It
#: is a statement of the kind its first argument names, one use at a time.
RESTATABLE_ENVS: tuple[str, ...] = ("restatable", "restatable*")

#: `list` is LaTeX's own list, the one the other three are built on.
LIST_ENVS: tuple[str, ...] = ("enumerate", "itemize", "description", "list")
#: Names the reader reads as something other than a statement, whatever a
#: paper declares them as.
RESERVED_ENVS: frozenset[str] = frozenset({"proof", *LIST_ENVS})


@dataclass(frozen=True, slots=True)
class EnvironmentTable:
    """The environments one paper writes beyond the reader's own, and what
    each one is.

    `entries` are the statement environments and the standard one each
    means: a paper may declare its own names — `\\newtheorem{thm}{Theorem}`
    — and a declared name means the standard environment its printed name
    says, or nothing. A `thm` is provable because `theorem` is. The
    standard table maps each of `STATEMENT_ENVS` to itself, and is what a
    paper that declares nothing reads. `containers` and `lists` are the
    frames and lists a paper's vocabulary map names.
    """

    entries: tuple[tuple[str, str | None], ...]
    containers: frozenset[str] = frozenset()
    lists: frozenset[str] = frozenset()
    #: Each name a vocabulary map says wraps an environment the reader knows
    #: by name (a proof, a display), and that environment.
    wrappers: tuple[tuple[str, str], ...] = ()
    #: tcolorbox's theorems (`\\newtcbtheorem`), each with the prefix its
    #: label takes: `\\begin{name}[options]{title}{label}` defines the label
    #: `<prefix>:<label>`, and `\\begin{name*}[options]{title}` none.
    boxed: tuple[tuple[str, str], ...] = ()

    @classmethod
    def standard(cls) -> EnvironmentTable:
        return cls(
            tuple((env, env) for env in STATEMENT_ENVS)
            + tuple((env, None) for env in RESTATABLE_ENVS)
        )

    def declaring(self, declared: Iterable[tuple[str, str | None]]) -> EnvironmentTable:
        """This table with the declared names added; a declaration wins.

        A paper that declares `\\newtheorem*{proof}{Proof}` still means a
        proof, and a declared list is still a list, so those names stay what
        the reader reads them as and never become statements.
        """
        table = dict(self.entries)
        table.update(
            (name, standard) for name, standard in declared if name not in RESERVED_ENVS
        )
        return replace(self, entries=tuple(table.items()))

    def mapping(
        self,
        statements: Iterable[tuple[str, str | None]],
        containers: Iterable[str],
        lists: Iterable[str],
        wrappers: Iterable[tuple[str, str]] = (),
    ) -> EnvironmentTable:
        """This table with a vocabulary map's names added. A name the table
        already knows keeps what it says, so a declaration wins over the map."""
        known = {name for name, _ in self.entries} | self.containers | self.lists
        known |= RESERVED_ENVS | {name for name, _ in self.wrappers}
        return replace(
            self,
            entries=self.entries
            + tuple((name, base) for name, base in statements if name not in known),
            containers=self.containers
            | {name for name in containers if name not in known},
            lists=self.lists | {name for name in lists if name not in known},
            wrappers=self.wrappers
            + tuple((name, inner) for name, inner in wrappers if name not in known),
        )

    def boxed_prefix(self, name: str) -> str | None:
        """The label prefix of the tcolorbox theorem `name` is a use of,
        starred or not, or `None` when it is none."""
        return dict(self.boxed).get(name.removesuffix("*"))

    def wrapped(self, name: str) -> str | None:
        """The environment `name` wraps, where a vocabulary map says so."""
        return dict(self.wrappers).get(name)

    @property
    def names(self) -> tuple[str, ...]:
        """The statement environments."""
        return tuple(name for name, _ in self.entries)

    @property
    def provable(self) -> frozenset[str]:
        """The names a trailing `\\begin{proof}` belongs to."""
        return frozenset(
            name for name, standard in self.entries if standard in PROVABLE_ENVS
        )

    def meaning(self, name: str) -> str | None:
        """The standard environment `name` is written for, or `None`."""
        return dict(self.entries).get(name)

    def pattern(self) -> str:
        """An alternation of every statement name, each escaped: `remark*` is
        a name."""
        return "|".join(re.escape(name) for name in self.names)


STANDARD_ENVIRONMENTS = EnvironmentTable.standard()
_PROOF_BEGIN_RE = re.compile(r"\\begin\{proof\}")
#: One run of mathematics, display first. The order matters: `$[^$]+$` alone
#: meets an inline `$$ ... $$` and pairs its closing delimiter with the prose
#: that follows, so from there on every run is off by one.
_MATH_RE = re.compile(r"\$\$[^$]+\$\$|\$[^$]+\$")

#: The commands that point at a label, the same in both formats. `\\ref` and
#: `\\autoref` are LaTeX's and hyperref's; `\\cref`, `\\Cref` and their range
#: forms are cleveref's, which takes a comma list; `\\eqref`, `\\vref` and
#: `\\nameref` print an equation number, a page-aware reference and a name.
#: `\\pageref` is not here: it names a page, not what is on it.
REFERENCE_COMMANDS: tuple[str, ...] = (
    "ref",
    "autoref",
    "cref",
    "Cref",
    "eqref",
    "vref",
    "Vref",
    "nameref",
)
RANGE_COMMANDS: tuple[str, ...] = ("crefrange", "Crefrange")
#: The commands whose argument is a comma list. Everywhere else a comma is
#: part of the label: `\\ref{lem:a,b}` names one label, as LaTeX reads it.
LIST_COMMANDS: frozenset[str] = frozenset({"cref", "Cref"})
#: A cleveref range names two labels, both of which the text points at.
RANGE_RE = re.compile(
    r"(?:(?P<word>[A-Za-z]+)~)?(?P<at>\\(?P<command>"
    + "|".join(RANGE_COMMANDS)
    + r")\*?\{(?P<first>[^}]+)\}\{(?P<last>[^}]+)\})"
)


@dataclass(frozen=True, slots=True)
class ReferenceTable:
    """The commands one paper points at labels with: the standard ones, and
    the ones its vocabulary map names. `lists` take a comma list; `prefixes`
    and `words` are a mapped command's label prefix and printed word."""

    commands: tuple[str, ...]
    lists: frozenset[str]
    prefixes: tuple[tuple[str, str], ...] = ()
    words: tuple[tuple[str, str], ...] = ()
    #: The commands a vocabulary map says define a label, as `\\label` does.
    label_commands: tuple[str, ...] = ()
    #: Each command a `restatable` declares, and the word it prints: it
    #: takes no argument and points at its own name, the label its
    #: `restatable` defines.
    restating: tuple[tuple[str, str | None], ...] = ()

    @classmethod
    def standard(cls) -> ReferenceTable:
        return cls(REFERENCE_COMMANDS, LIST_COMMANDS)

    def mapping(
        self, entries: Iterable[tuple[str, bool, str, str | None]]
    ) -> ReferenceTable:
        """This table with a vocabulary map's commands added, each
        `(command, takes_list, prefix, word)`. A command the table already
        knows keeps what it says, and a range command or `\\label` is never
        one."""
        known = set(self.commands) | set(RANGE_COMMANDS) | {"label", "S"}
        added = [entry for entry in entries if entry[0] not in known]
        return replace(
            self,
            commands=self.commands + tuple(command for command, *_ in added),
            lists=self.lists | {command for command, listed, *_ in added if listed},
            prefixes=self.prefixes
            + tuple((command, prefix) for command, _, prefix, _ in added if prefix),
            words=self.words
            + tuple((command, word) for command, _, _, word in added if word),
        )

    def overriding(
        self, entries: Iterable[tuple[str, bool, str, str | None]]
    ) -> ReferenceTable:
        """This table with its own commands read anew, each
        `(command, takes_list, prefix, word)`: a package or the paper that
        redefines a standard command (refstyle's `\\eqref`, which adds
        `eq:`). A command it does not know is not added."""
        read = [entry for entry in entries if entry[0] in self.commands]
        named = {command for command, *_ in read}
        return replace(
            self,
            lists=(self.lists - named)
            | {command for command, listed, *_ in read if listed},
            prefixes=tuple(p for p in self.prefixes if p[0] not in named)
            + tuple((command, prefix) for command, _, prefix, _ in read if prefix),
            words=tuple(w for w in self.words if w[0] not in named)
            + tuple((command, word) for command, _, _, word in read if word),
        )

    def defining(self, commands: Iterable[str]) -> ReferenceTable:
        """This table with a vocabulary map's label commands added; a
        reference command is never one."""
        known = set(self.commands) | set(RANGE_COMMANDS) | {"label"}
        added = tuple(command for command in commands if command not in known)
        return replace(self, label_commands=self.label_commands + added)

    def pattern(self) -> re.Pattern[str]:
        """One reference: the word before it (`Theorem~`), the command, and
        its label argument."""
        return _reference_re(self.commands)

    def restating_pattern(self) -> re.Pattern[str] | None:
        """A restating command, starred or not, or `None` when there is
        none."""
        if not self.restating:
            return None
        return _restating_re(tuple(command for command, _ in self.restating))

    def label_pattern(self) -> re.Pattern[str] | None:
        """A mapped label command and its label, or `None` when there is
        none."""
        if not self.label_commands:
            return None
        return _label_re(self.label_commands)


@functools.cache
def _reference_re(commands: tuple[str, ...]) -> re.Pattern[str]:
    # an optional argument (fancyref's `\\fref[vario]{…}`) is never the label
    return re.compile(
        r"(?:(?P<word>[A-Za-z]+)~)?(?P<at>\\(?P<command>S\\ref|"
        + "|".join(re.escape(command) for command in commands)
        + r")\*?(?:\[[^\]\n]*\])?\{(?P<label>[^}]+)\})"
    )


@functools.cache
def _restating_re(commands: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(
        r"\\(?P<command>"
        + "|".join(re.escape(command) for command in commands)
        + r")(?![a-zA-Z@])\*?"
    )


@functools.cache
def _label_re(commands: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(
        r"\\(?:"
        + "|".join(re.escape(command) for command in commands)
        + r")\{([^}]+)\}"
    )


STANDARD_REFERENCES = ReferenceTable.standard()


class FoundReference(NamedTuple):
    """One reference command: where it stands in the text it was read from,
    the word before it, the command as written, and the labels it names."""

    start: int
    end: int
    word: str
    command: str
    targets: tuple[str, ...]


def references(
    text: str, table: ReferenceTable = STANDARD_REFERENCES
) -> list[FoundReference]:
    """Every reference command a text holds, in order.

    A cleveref list `\\cref{a,b}` names two targets and a range two more.
    Only cleveref's commands take a list; `\\ref{a,b}` is the one label `a,b`.
    A restating command (`\\main*`) points at its own name. `start` and
    `end` bound the command, not the word before it.
    """
    found: list[FoundReference] = []
    prefixes = dict(table.prefixes)
    words = dict(table.words)
    for match in table.pattern().finditer(text):
        command = match.group("command")
        label = match.group("label")
        prefix = prefixes.get(command, "")
        targets = tuple(
            prefix + target.strip()
            for target in (label.split(",") if command in table.lists else [label])
            if target.strip()
        )
        if targets:
            found.append(
                FoundReference(
                    match.start("at"),
                    match.end("at"),
                    match.group("word") or words.get(command, ""),
                    command,
                    targets,
                )
            )
    restating = table.restating_pattern()
    if restating is not None:
        restated = dict(table.restating)
        for match in restating.finditer(text):
            command = match.group("command")
            found.append(
                FoundReference(
                    match.start(),
                    match.end(),
                    restated[command] or "",
                    command,
                    (command,),
                )
            )
    for match in RANGE_RE.finditer(text):
        found.append(
            FoundReference(
                match.start("at"),
                match.end("at"),
                match.group("word") or "",
                match.group("command"),
                (match.group("first").strip(), match.group("last").strip()),
            )
        )
    return sorted(found, key=lambda item: item.start)


_YAML_FENCE_RE = re.compile(r"^(?:---|\.\.\.)\s*$")
_YAML_TITLE_RE = re.compile(r"^title:[ \t]*(?P<value>.*?)[ \t]*$")
_HEADER_INCLUDES_RE = re.compile(r"^header-includes:[ \t]*(?P<value>.*?)[ \t]*$")
#: A YAML block scalar's indicator: its lines are the value, as written.
_BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?$")
#: A plain YAML value ends at a ` #` comment.
_YAML_COMMENT_RE = re.compile(r"(?:^|[ \t])#")


def _line_starts(lines: Sequence[str]) -> list[int]:
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    return starts


def yaml_header(text: str) -> tuple[int, int, tuple[int, int] | None] | None:
    """A Markdown file's YAML header, as `(start, end, title)` offsets.

    The header opens with `---` on the first line and closes with `---` or
    `...`. Its `title:` value is read by lines, not parsed: a
    single-line value, its quotes dropped.
    """
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return None
    starts = _line_starts(lines)
    title: tuple[int, int] | None = None
    for number in range(1, len(lines)):
        line = lines[number]
        if _YAML_FENCE_RE.match(line):
            return (0, starts[number] + len(line), title)
        found = _YAML_TITLE_RE.match(line)
        if found is not None and title is None and found.group("value"):
            start = starts[number] + found.start("value")
            end = starts[number] + found.end("value")
            value = found.group("value")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                start, end = start + 1, end - 1
            title = (start, end)
    return None


def header_includes(text: str) -> list[tuple[int, int]]:
    """The LaTeX a Markdown file's YAML header passes to pandoc's preamble,
    `header-includes`, as `(start, end)` offsets, one per line.

    Read by lines, not parsed: the key's own value, then its items (`- …`)
    and continuation lines up to the next key. A plain value ends at a ` #`
    comment; a block scalar's (`|`, `>`) lines are taken whole.
    """
    header = yaml_header(text)
    if header is None:
        return []
    lines = text[: header[1]].split("\n")
    starts = _line_starts(lines)
    found: list[tuple[int, int]] = []
    inside = False
    block = False
    for number, line in enumerate(lines[1:-1], start=1):
        key = _HEADER_INCLUDES_RE.match(line)
        if key is not None:
            inside = True
            value = key.group("value")
            block = _take(found, starts[number], line, key.start("value"), value)
            continue
        if not inside:
            continue
        if line[:1] not in ("", " ", "\t", "-", "#"):
            break
        stripped = line.lstrip()
        column = len(line) - len(stripped)
        if stripped.startswith("- "):
            value = stripped[2:]
            block = _take(found, starts[number], line, column + 2, value.strip())
        elif stripped.startswith("#") and not block:
            continue
        elif stripped:
            if block:
                found.append((starts[number] + column, starts[number] + len(line)))
            else:
                _take(found, starts[number], line, column, stripped)
    return found


def _take(
    found: list[tuple[int, int]], start: int, line: str, column: int, value: str
) -> bool:
    """Add a plain value's LaTeX, up to its comment; whether it opens a
    block scalar instead."""
    if _BLOCK_SCALAR_RE.match(value):
        return True
    rest = line[column:]
    comment = _YAML_COMMENT_RE.search(rest)
    end = column + len((rest[: comment.start()] if comment else rest).rstrip())
    if line[column:end].strip():
        found.append((start + column, start + end))
    return False


#: A LaTeX command that pulls another file into this one. `\import` and
#: `\subimport` name a directory and a file, the other three a path. The
#: brace directly after the name keeps `\includegraphics` and `\includeonly`
#: out.
PATH_INCLUDE_RE = re.compile(
    r"\\(?P<command>input|include|subfile)\{(?P<path>[^}]*)\}"
    r"|\\(?P<importer>import|subimport)\*?\{(?P<directory>[^}]*)\}\{(?P<name>[^}]*)\}"
)
_BEGIN_DOCUMENT_RE = re.compile(r"\\begin\{document\}")
#: Where a `%` is text rather than a comment: inline verbatim, whose
#: delimiter is the character after `\verb`, and a URL argument.
_LITERAL_RE = re.compile(r"\\verb\*?([^a-zA-Z\s*])(.*?)\1|\\(?:url|href)\{[^}]*\}")


def comment_start(line: str) -> int | None:
    """The column where a line's LaTeX comment begins, or `None`.

    A `%` starts a comment unless an odd run of backslashes escapes it, so
    `\\%` is a percent sign and `\\\\%` is a line break followed by a comment.
    A `%` inside `\\verb|...|` or a `\\url{...}` is text.
    """
    literal = [(m.start(), m.end()) for m in _LITERAL_RE.finditer(line)]
    for index, char in enumerate(line):
        if char != "%":
            continue
        if any(start <= index < end for start, end in literal):
            continue
        slashes = len(line[:index]) - len(line[:index].rstrip("\\"))
        if slashes % 2 == 0:
            return index
    return None


def uncommented(line: str) -> str:
    """The part of a line before its LaTeX comment, if it has one."""
    start = comment_start(line)
    return line if start is None else line[:start]


class FoundInclude(NamedTuple):
    """One include command: its columns, its command and its path as written."""

    start: int
    end: int
    command: str
    target: str


def include_targets(line: str) -> list[FoundInclude]:
    """The includes a line (or a text) names, comment excluded, as written.

    A path built from a macro keeps its backslash; nothing here expands it.
    """
    found: list[FoundInclude] = []
    for match in PATH_INCLUDE_RE.finditer(uncommented(line)):
        if match.group("command"):
            command, target = match.group("command"), match.group("path")
        else:
            command = match.group("importer")
            target = match.group("directory") + match.group("name")
        found.append(FoundInclude(match.start(), match.end(), command, target.strip()))
    return found


def path_includes(
    lines: list[str], fence_map: list[bool]
) -> list[tuple[int, FoundInclude]]:
    """Every include in the body, as `(1-based line, include)`.

    The body starts after `\\begin{document}` where the file has one. A
    commented-out include and one in a fence are not includes.
    """
    start = next(
        (
            number + 1
            for number, line in enumerate(lines)
            if not fence_map[number] and _BEGIN_DOCUMENT_RE.search(uncommented(line))
        ),
        0,
    )
    found: list[tuple[int, FoundInclude]] = []
    for number in range(start, len(lines)):
        if fence_map[number]:
            continue
        found.extend((number + 1, target) for target in include_targets(lines[number]))
    return found


#: What LaTeX appends to a path that names no extension.
TEX_SUFFIX = ".tex"


def resolved_include(
    file: Path, target: str, root: Path, suffix: str = TEX_SUFFIX
) -> tuple[Path, ...]:
    """Where an include's path may point: the paper root, then the file's own
    directory.

    LaTeX resolves `\\input` against the directory it runs in, which is the
    root file's; `\\subimport` resolves against the importing file. The two
    agree for a root, and the tool cannot know which directory the author
    compiles in, so both are candidates. A package or a class names no
    extension and takes its own, `suffix`.
    """
    name = target if Path(target).suffix else target + suffix
    bases = dict.fromkeys((root, file.parent))
    return tuple(Path(os.path.normpath(base / name)) for base in bases)


class EnvEnd(NamedTuple):
    """Where an environment closes: the 0-based line, the column just past
    its `\\end{…}`, and whether anything closed it. When nothing does, it is
    the end of the last line."""

    line: int
    column: int
    closed: bool


class TrailingProof(NamedTuple):
    """A proof that follows an environment: where its `\\begin{proof}`
    stands, and where it ends."""

    line: int
    column: int
    end: EnvEnd


class MdLatexFormat:
    """Line mechanics for one Markdown+LaTeX file.

    An instance reads one paper's environment table: which names bound a
    statement, and which of them a trailing proof belongs to.
    """

    name = "md-latex"

    def __init__(self, environments: EnvironmentTable = STANDARD_ENVIRONMENTS) -> None:
        self.environments = environments
        self.statement_envs = environments.names
        self.provable_envs = environments.provable
        names = environments.pattern()
        self.env_begin_re = re.compile(r"\\begin\{(" + names + r")\}")
        self.env_end_re = re.compile(r"\\end\{(" + names + r")\}")
        self._env_title_re = re.compile(r"\\begin\{(?:" + names + r")\}\[([^\]]*)\]")

    # --- Line processing ---

    @staticmethod
    def directives(lines: Sequence[str], fence_map: Sequence[bool]) -> list[Directive]:
        """Every directive the file carries outside a fence, in reading order.

        A directive in a code fence is an example of one, not a mark on the
        paper.
        """
        return [
            found
            for found in parse_directives(lines, DIRECTIVE_RE)
            if not fence_map[found.line - 1]
        ]

    @staticmethod
    def readable(lines: Sequence[str]) -> list[str]:
        """The lines the syntax passes read. Markdown hides nothing inline."""
        return list(lines)

    @staticmethod
    def unmarked(line: str) -> str:
        """A readable line with its directives blanked, length kept."""
        return strip_directives(line)

    @staticmethod
    def is_markup(line: str) -> bool:
        """A line that carries a directive or a `\\label{}` and no text."""
        return not LABEL_RE.sub("", strip_directives(line)).strip()

    @staticmethod
    def strip_line(line: str) -> str:
        """Drop structural LaTeX and heading markers, collapse inline math.

        The normalization for content hashes, so a reflowed paragraph or a
        moved `\\label` does not change one.
        """
        text = STRIP_RE.sub("", line.strip())
        text = re.sub(r"^#+\s*", "", text)
        text = _MATH_RE.sub(lambda m: m.group(0).replace(" ", ""), text)
        return text.strip()

    @staticmethod
    def heading_level(line: str) -> int | None:
        match = HEADING_RE.match(line)
        return len(match.group(1)) if match else None

    @staticmethod
    def heading_title(line: str) -> str:
        """The heading text with any `\\label{}` and pandoc attributes removed."""
        match = HEADING_RE.match(line)
        if not match:
            return ""
        text = HEADING_ATTRIBUTES_RE.sub("", match.group(2))
        return LABEL_RE.sub("", text).strip()

    @classmethod
    def heading_title_at(cls, lines: Sequence[str], index: int) -> str:
        """The title of the heading at `lines[index]`. One line in Markdown."""
        return cls.heading_title(lines[index])

    @classmethod
    def heading_end(cls, lines: Sequence[str], index: int) -> tuple[int, int]:
        """Where the heading at `lines[index]` ends: `(line, column)`, the
        column just past it. A Markdown heading is its line."""
        return (index, len(lines[index]))

    @staticmethod
    def heading_attributes(line: str) -> tuple[int, int, str | None, bool] | None:
        """pandoc's `{#id .class}` on a heading line: its columns, the id it
        gives, and whether it unnumbers the heading. `None` when there are
        none."""
        if not HEADING_RE.match(line):
            return None
        match = HEADING_ATTRIBUTES_RE.search(line)
        if match is None:
            return None
        body = match.group("body")
        found = _ATTRIBUTE_ID_RE.search(body)
        start = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
        end = match.end() - (len(match.group(0)) - len(match.group(0).rstrip()))
        return (
            start,
            end,
            found.group("id") if found else None,
            _UNNUMBERED_RE.search(body) is not None,
        )

    @classmethod
    def heading_starred(cls, line: str) -> bool:
        """Whether a heading is unnumbered: pandoc's `.unnumbered` or `-`."""
        found = cls.heading_attributes(line)
        return found is not None and found[3]

    def env_title(self, line: str) -> str | None:
        """The optional `[Title]` of a statement environment."""
        match = self._env_title_re.search(line)
        return match.group(1).strip() if match else None

    @staticmethod
    def build_fence_map(lines: list[str]) -> list[bool]:
        """Mark the lines inside ``` code fences, opening line included."""
        fence = False
        result: list[bool] = []
        for line in lines:
            if line.strip().startswith("```"):
                fence = not fence
            result.append(fence)
        return result

    # --- Environment ranges ---

    @staticmethod
    def find_env_end(
        lines: Sequence[str],
        start_idx: int,
        env_type: str,
        fence_map: Sequence[bool] | None = None,
        begin_column: int | None = None,
    ) -> EnvEnd:
        """Where the environment opened at `lines[start_idx]` closes.

        `begin_column` is where its `\\begin` stands; the first one on the
        line when not given. Every `\\begin` and `\\end` of the same name after
        it is counted in the order it stands, so nested copies and a pair on
        one line close where LaTeX closes them. A fenced line is skipped.
        Nothing closing it gives the end of the last line, unclosed.
        """
        begin_pat = f"\\begin{{{env_type}}}"
        end_pat = f"\\end{{{env_type}}}"
        tokens = re.compile(re.escape(begin_pat) + "|" + re.escape(end_pat))
        start_line = lines[start_idx]
        if begin_column is None:
            found = start_line.find(begin_pat)
            begin_column = found if found >= 0 else 0
        depth = 0
        for index in range(start_idx, len(lines)):
            if index > start_idx and fence_map and fence_map[index]:
                continue
            origin = begin_column if index == start_idx else 0
            for token in tokens.finditer(lines[index], origin):
                if token.group(0) == begin_pat:
                    depth += 1
                    continue
                depth -= 1
                if depth == 0:
                    return EnvEnd(index, token.end(), True)
        last = len(lines) - 1
        return EnvEnd(last, len(lines[last]), False)

    @staticmethod
    def find_trailing_proof(
        lines: Sequence[str],
        env_end_idx: int,
        fence_map: Sequence[bool] | None = None,
        max_gap: int = 5,
        end_column: int | None = None,
        proofs: Sequence[str] = ("proof",),
    ) -> TrailingProof | None:
        """The proof attached to the environment ending at `env_end_idx`.

        A proof is attached only when it is the first non-blank thing after
        the environment; prose in between means the proof stands alone. With
        `end_column`, the rest of the closing line is read first. `proofs`
        are the names a proof goes by, a map's wrapper of one among them.
        """
        opening = (
            _PROOF_BEGIN_RE
            if tuple(proofs) == ("proof",)
            else re.compile(
                r"\\begin\{(" + "|".join(re.escape(p) for p in proofs) + r")\}"
            )
        )

        def name_of(found: re.Match[str]) -> str:
            return found.group(1) if found.groups() else "proof"

        if end_column is not None:
            rest = lines[env_end_idx][end_column:]
            if rest.strip():
                column = end_column + len(rest) - len(rest.lstrip())
                found = opening.match(lines[env_end_idx], column)
                if not found:
                    return None
                end = MdLatexFormat.find_env_end(
                    lines, env_end_idx, name_of(found), fence_map, column
                )
                return TrailingProof(env_end_idx, column, end)
        for j in range(env_end_idx + 1, min(env_end_idx + 1 + max_gap, len(lines))):
            if fence_map and fence_map[j]:
                continue
            if not lines[j].strip():
                continue
            found = opening.search(lines[j])
            if found:
                end = MdLatexFormat.find_env_end(
                    lines, j, name_of(found), fence_map, found.start()
                )
                return TrailingProof(j, found.start(), end)
            break
        return None
