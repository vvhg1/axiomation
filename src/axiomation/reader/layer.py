"""The passes that fill the document layer, file by file, in reading order.

Passes over environments, headings, references, citations and quotation
sites.

Each file is read through two masks, both the file's own length so every
offset is a character of its text:

- **visible**: the text with comments, `\\iffalse` blocks, verbatim bodies,
  Markdown code fences, HTML comments and the YAML header blanked. The
  declarations, the macros, the includes and the title are read here.
- **body**: `visible` with the preamble and the end matter blanked too.
  Sections, environments, labels, references, citations and footnotes are
  read here.

What renders nothing is painted into the file's hidden map, in an order
where a later coat wins: structure, front matter, comments, directives,
`\\iffalse`, then the preamble and the end matter. The title's argument is
then rendered even in a preamble, and the coats inside it painted again.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from axiomation.reader.declarations import (
    argument_groups,
    declaring_text,
    environment_table,
    read_declarations,
    reference_table,
)
from axiomation.reader.directives import DIRECTIVE_RE, DirectiveKind, split_list
from axiomation.reader.document import (
    COMMAND,
    ENVIRONMENT,
    Citation,
    Comment,
    CommentKind,
    Declaration,
    Directive,
    Document,
    EnvEntry,
    Environment,
    EnvKind,
    FileRole,
    Footnote,
    Format,
    Hidden,
    HiddenKind,
    Include,
    Label,
    Macro,
    MapAnswer,
    Quotation,
    Reference,
    Section,
    SourceFile,
    Span,
    Vocabulary,
    VocabularyKind,
)
from axiomation.reader.environments import static_kind
from axiomation.reader.formats import format_for
from axiomation.reader.formats.latex import (
    BEGIN_DOCUMENT_RE,
    COMMENT_ENV,
    END_DOCUMENT_RE,
    STRUCTURE_RE,
    TEX_DIRECTIVE_RE,
    braced_end,
    read_mask,
)
from axiomation.reader.formats.md_latex import (
    LABEL_RE,
    RESTATABLE_ENVS,
    STANDARD_REFERENCES,
    STATEMENT_ENVS,
    EnvironmentTable,
    ReferenceTable,
    references,
    yaml_header,
)
from axiomation.reader.includes import Walk
from axiomation.reader.sentences import sentence_at

#: One citation command, its optional groups and its keys.
CITE_RE = re.compile(r"\\([a-zA-Z]*cite[a-zA-Z]*)((?:\[[^\]]*\])*)\{([^}]+)\}")
#: The title a proof names in its optional argument. The cutter
#: reads it to attach a proof to what it proves.
PROOF_TITLE_RE = re.compile(r"\\begin\{proof\}\[([^\]]*)\]")
#: A heading that says it is a proof: "Proof of
#: Theorem~\ref{t}", "3.2 Proofs of ...", "A sketch of the proof of ...".
#: What it proves is the first reference in the title.
PROOF_HEADING_RE = re.compile(
    r"^(?:(?:\d[\d.]*|[A-Z](?:\.\d+)*)\.?\s+)?"
    r"(?:(?:a|the)\s+)?(?:sketch(?:es)?\s+of\s+(?:the\s+)?)?proofs?\b",
    re.IGNORECASE,
)
ENV_BEGIN_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
#: The environments that hold a block quotation;
#: the inline quotation macros, `\\enquote*` the same site; and the words
#: under which a double-quoted span is a term, not a quotation.
QUOTE_ENVS: frozenset[str] = frozenset({"quote", "quotation", "displayquote"})
ENQUOTE_RE = re.compile(r"\\(?:enquote|textquote)\*?")
QUOTE_MIN_WORDS = 8

#: The names a display written with delimiters, and a code fence, go by.
BRACKET_DISPLAY = "\\["
DOLLAR_DISPLAY = "$$"
CODE_FENCE = "```"

#: Commands that lay out a page and print nothing. A line that holds only
#: these, labels and `STRUCTURE_RE`'s commands is structure, never a block
#: of its own. Each addition comes with a test, and each family is taken
#: whole.
LAYOUT_COMMANDS: tuple[str, ...] = (
    "allowdisplaybreaks",
    "newpage",
    "clearpage",
    "cleardoublepage",
    "pagebreak",
    "nopagebreak",
    "enlargethispage",
    "FloatBarrier",
    "vspace",
    "smallskip",
    "medskip",
    "bigskip",
    "centering",
    "tiny",
    "scriptsize",
    "footnotesize",
    "small",
    "normalsize",
    "large",
    "Large",
    "LARGE",
    "huge",
    "Huge",
)
_LAYOUT_RE = re.compile(
    r"\\(?:" + "|".join(LAYOUT_COMMANDS) + r")(?![a-zA-Z@])\*?"
    r"(?:\[[^\]]*\])?(?:\{[^{}]*\})?"
)
#: The front matter that is metadata, not text. `\\title` is
#: not here: its argument is rendered.
FRONT_MATTER_COMMANDS: tuple[str, ...] = (
    "author",
    "date",
    "thanks",
    "affil",
    "affiliation",
    "address",
    "email",
    "institute",
    "orcid",
    "keywords",
    "titlerunning",
    "authorrunning",
    "titletag",
)
_FRONT_MATTER_RE = re.compile(
    r"\\(?:" + "|".join(FRONT_MATTER_COMMANDS) + r")(?![a-zA-Z@])\*?\s*"
    r"(?:\[[^\]]*\]\s*)?(?=\{)"
)
_TITLE_RE = re.compile(r"\\title(?![a-zA-Z@])\s*(?:\[[^\]]*\]\s*)?(?=\{)")
_FOOTNOTE_RE = re.compile(r"\\footnote(?![a-zA-Z@])\s*(?:\[[^\]]*\]\s*)?(?=\{)")
_MACRO_RE = re.compile(
    r"\\(newcommand|renewcommand|providecommand|DeclareRobustCommand"
    r"|DeclareMathOperator|newenvironment|renewenvironment|def|gdef|edef|xdef)"
    r"(?![a-zA-Z@])\*?"
)
_DEF_COMMANDS = frozenset({"def", "gdef", "edef", "xdef"})
_ENVIRONMENT_COMMANDS = frozenset({"newenvironment", "renewenvironment"})
_CONTROL_SEQUENCE_RE = re.compile(r"\\(?:[a-zA-Z@]+|.)")
_PARAMETER_RE = re.compile(r"#[1-9]")
_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)


def read_layer(walk: Walk, vocabulary: Vocabulary | None = None) -> Document:
    """The document layer of the files a walk read, its tables the paper's
    declarations and then its vocabulary map."""
    order = ReadingOrder(walk.files, walk.includes)
    declarations = read_declarations(walk.files, order.key)
    table = environment_table(declarations)
    referring = replace(
        reference_table(walk.files),
        restating=_restating(walk.files, declarations),
    )
    regions: dict[tuple[str, str], list[_Region]] = {}
    if vocabulary is not None:
        base, read_from = _in_force(vocabulary, walk.files, order)
        regions = {
            place: [_Region(at, *_mapped(table, referring, v)) for at, v in found]
            for place, found in read_from.items()
        }
        table, referring = _mapped(table, referring, base)
    items = [
        _FileReader(source, table, referring, order, regions).read()
        for source in walk.files
    ]
    files = tuple(
        replace(source, hidden=found.hidden)
        for source, found in zip(walk.files, items, strict=True)
    )
    titles = order.sort(
        (found.title for found in items if found.title is not None), key=_itself
    )
    root = walk.files[0]

    def gathered(name: str, key) -> tuple:
        return tuple(order.sort((x for f in items for x in getattr(f, name)), key))

    return Document(
        format=root.format,
        root=root.path,
        files=files,
        includes=walk.includes,
        never_included=walk.never_included,
        declarations=tuple(order.sort(declarations, key=_at)),
        title=titles[0] if titles else None,
        sections=gathered("sections", lambda item: item.heading),
        environments=gathered("environments", lambda item: item.span),
        labels=gathered("labels", _at),
        references=gathered("references", _at),
        citations=gathered("citations", _at),
        quotations=gathered("quotations", _at),
        footnotes=gathered("footnotes", _at),
        macros=gathered("macros", _at),
        comments=gathered("comments", _at),
        directives=gathered("directives", _at),
        vocabulary=vocabulary if vocabulary is not None else Vocabulary(),
    )


def _restating(
    files: Sequence[SourceFile], declarations: Sequence[Declaration]
) -> tuple[tuple[str, str | None], ...]:
    """Each command a `restatable` declares, and the word it prints: its
    statement's printed name."""
    printed = {found.name: found.printed for found in declarations}
    found: dict[str, str | None] = {}
    for source in files:
        if source.role not in (FileRole.ROOT, FileRole.BODY):
            continue
        text = declaring_text(source) if source.format is Format.LATEX else source.text
        for match in ENV_BEGIN_RE.finditer(text):
            if match.group(1) not in RESTATABLE_ENVS:
                continue
            restatable = _restatable(text, match.end())
            if restatable is None:
                continue
            base, command = restatable.base, restatable.command
            word = printed.get(base) or (
                base[:1].upper() + base[1:] if base in STATEMENT_ENVS else None
            )
            found.setdefault(command, word)
    return tuple(found.items())


@dataclass(frozen=True, slots=True)
class _Restatable:
    """A `restatable`'s opening arguments: its title, the statement it is,
    the command it declares and where that name stands, and where its body
    starts."""

    title: str | None
    base: str
    command: str
    command_at: tuple[int, int]
    body_start: int


def _restatable(text: str, after: int) -> _Restatable | None:
    """The arguments after `\\begin{restatable}`: `[title]{base}{command}`."""
    groups = []
    start = after
    for kind, body, end in argument_groups(text, after):
        groups.append((kind, body, start, end))
        start = end
        if sum(1 for group in groups if group[0] == "{") == 2:
            break
    braced = [group for group in groups if group[0] == "{"]
    if len(braced) != 2 or not braced[0][1].strip() or not braced[1][1].strip():
        return None
    title = next((g[1].strip() for g in groups if g[0] == "["), None)
    _, command, command_start, command_end = braced[1]
    opening = text.index("{", command_start)
    return _Restatable(
        title,
        braced[0][1].strip(),
        command.strip(),
        (opening, command_end),
        command_end,
    )


@dataclass(frozen=True, slots=True)
class _Boxed:
    """A tcolorbox theorem's opening arguments: its title, its label and
    where that stands (a starred use has none), and where its body
    starts."""

    title: str | None
    label: tuple[str, tuple[int, int]] | None
    body_start: int


def _boxed(text: str, after: int, starred: bool) -> _Boxed | None:
    """The arguments after `\\begin{name}`: `[options][toc title]{title}`,
    then `{label}` where it is not starred."""
    wanted = 1 if starred else 2
    braced: list[tuple[str, int, int]] = []
    start = after
    for kind, body, end in argument_groups(text, after):
        if kind == "{":
            braced.append((body, start, end))
        elif braced:
            break
        start = end
        if len(braced) == wanted:
            break
    if len(braced) != wanted:
        return None
    title = braced[0][0].strip() or None
    label = None
    if not starred and braced[1][0].strip():
        _, label_start, label_end = braced[1]
        opening = text.index("{", label_start)
        label = (braced[1][0].strip(), (opening, label_end))
    return _Boxed(title, label, braced[-1][2])


@dataclass(frozen=True, slots=True)
class _Region:
    """Where one declaration of a name declared more than once comes into
    force (`at`, a reading-order key), and the tables the name reads under
    from there on."""

    at: tuple[int, int, int]
    table: EnvironmentTable
    referring: ReferenceTable


def _in_force(
    vocabulary: Vocabulary, files: Sequence[SourceFile], order: ReadingOrder
) -> tuple[
    Vocabulary, dict[tuple[str, str], list[tuple[tuple[int, int, int], Vocabulary]]]
]:
    """A map with an answer for each of several declaring texts of one name
    (`MapAnswer`), read where each declaration is in force: the map with
    each such name's first declaration's answer, and for each such name
    where each declaration stands, the map with its answer. A use is read
    under the last declaration before it, the first where none is, as
    `vocabulary.in_force` lists it; a text found nowhere is not read."""
    by_name: dict[tuple[str, str], list[MapAnswer]] = {}
    for answer in vocabulary.answers:
        by_name.setdefault((answer.namespace, answer.name), []).append(answer)
    several = {
        place: answers
        for place, answers in by_name.items()
        if len({a.declaration for a in answers}) > 1
    }
    if not several:
        return vocabulary, {}
    visible = [(source.path, source.text, declaring_text(source)) for source in files]
    starts: dict[tuple[str, str], list[tuple[tuple[int, int, int], MapAnswer]]] = {}
    for place, answers in several.items():
        found = []
        for answer in answers:
            text = answer.declaration
            if not text:
                continue
            for path, whole, shown in visible:
                at = whole.find(text)
                while at >= 0:
                    if shown[at] == whole[at]:
                        found.append(
                            (order.key(Span(path, at, at + len(text))), answer)
                        )
                    at = whole.find(text, at + 1)
        if found:
            starts[place] = sorted(found, key=lambda item: item[0])
    # a name whose declaring texts are found nowhere reads its first answer
    first = {place: answers[0] for place, answers in several.items()}
    first |= {place: found[0][1] for place, found in starts.items()}
    kept = [
        a
        for a in vocabulary.answers
        if (a.namespace, a.name) not in first or a is first[(a.namespace, a.name)]
    ]
    return Vocabulary.of(kept), {
        place: [
            (
                at,
                Vocabulary.of(
                    [a for a in kept if (a.namespace, a.name) != place] + [answer]
                ),
            )
            for at, answer in found
        ]
        for place, found in starts.items()
    }


def _mapped(
    table: EnvironmentTable, referring: ReferenceTable, vocabulary: Vocabulary
) -> tuple[EnvironmentTable, ReferenceTable]:
    """The tables with a vocabulary map's entries added. An environment the
    reader reads by its own name (a display, a float, a frame) keeps it."""
    entries = [
        entry for entry in vocabulary.environments if static_kind(entry.name) is None
    ]

    def named(kind: VocabularyKind) -> list[EnvEntry]:
        return [entry for entry in entries if entry.kind is kind]

    # a wrapper of a statement is a statement of its meaning; of another
    # environment the reader knows by name, read as that one; of anything
    # else, nothing (a map's gate refuses it)
    wrapping = [(e.name, e.base) for e in named(VocabularyKind.WRAPS) if e.base]
    statements = [(e.name, e.base) for e in named(VocabularyKind.STATEMENT)]
    statements += [
        (name, table.meaning(inner)) for name, inner in wrapping if inner in table.names
    ]
    wrappers = [
        (name, inner)
        for name, inner in wrapping
        if inner not in table.names and static_kind(inner) is not None
    ]
    return (
        table.mapping(
            statements,
            (entry.name for entry in named(VocabularyKind.CONTAINER)),
            (entry.name for entry in named(VocabularyKind.LIST)),
            wrappers,
        ),
        referring.mapping(
            (entry.command, entry.takes_list, entry.prefix, entry.word)
            for entry in vocabulary.references
        ).defining(vocabulary.labels),
    )


def _at(item) -> Span:
    return item.at


_COMMAND_AT = re.compile(r"\\([A-Za-z@]+)")


def _command_at(text: str, at: int) -> str:
    """The name of the command that starts at `at`."""
    match = _COMMAND_AT.match(text, at)
    return match.group(1) if match is not None else ""


def _itself(span: Span) -> Span:
    return span


class ReadingOrder:
    """Where a character stands in the order LaTeX reads the paper.

    The walk splices each file it followed into its includer, at the end of
    the include: the includer's text up to there, then the included file,
    then the rest. Each stretch is a segment, numbered in reading order.
    `files[0]` is the root. The cutter orders its blocks by the same key
    (`of`).
    """

    def __init__(
        self, files: Sequence[SourceFile], includes: Sequence[Include]
    ) -> None:
        followed: dict[str, list[tuple[int, str]]] = {}
        reached = {files[0].path}
        for include in includes:
            if include.resolved is not None and include.resolved not in reached:
                reached.add(include.resolved)
                followed.setdefault(include.at.file, []).append(
                    (include.at.end, include.resolved)
                )
        lengths = {source.path: len(source.text) for source in files}
        self._segments: dict[str, list[tuple[int, int]]] = {}
        counter = 0

        def visit(path: str) -> None:
            nonlocal counter
            position = 0
            for end, target in followed.get(path, []):
                self._segments.setdefault(path, []).append((position, counter))
                counter += 1
                visit(target)
                position = end
            self._segments.setdefault(path, []).append((position, counter))
            counter += 1
            del lengths[path]

        visit(files[0].path)
        # A file the walk read but never spliced, which cannot happen for a
        # walk's own output, still gets a place: after everything else.
        for path in list(lengths):
            self._segments[path] = [(0, counter)]
            counter += 1

    @classmethod
    def of(cls, document: Document) -> ReadingOrder:
        """The reading order of a document layer: its includes are in
        reading order, and each includer's in the order they stand, as the
        walk's are."""
        return cls(document.files, document.includes)

    def key(self, span: Span) -> tuple[int, int, int]:
        segments = self._segments[span.file]
        starts = [start for start, _ in segments]
        index = max(bisect.bisect_right(starts, span.start) - 1, 0)
        return (segments[index][1], span.start, -span.end)

    def sort(self, items: Iterable, key) -> list:
        return sorted(items, key=lambda item: self.key(key(item)))


@dataclass
class _Found:
    """One file's items, before they are merged in reading order."""

    hidden: tuple[Hidden, ...] = ()
    title: Span | None = None
    sections: list[Section] = field(default_factory=list)
    environments: list[Environment] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    quotations: list[Quotation] = field(default_factory=list)
    footnotes: list[Footnote] = field(default_factory=list)
    macros: list[Macro] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    directives: list[Directive] = field(default_factory=list)


#: The order hidden kinds are painted in; a later coat wins.
_COATS: tuple[HiddenKind, ...] = (
    HiddenKind.STRUCTURE,
    HiddenKind.FRONT_MATTER,
    HiddenKind.COMMENT,
    HiddenKind.DIRECTIVE,
    HiddenKind.DISABLED,
)
_REGIONS: tuple[HiddenKind, ...] = (HiddenKind.PREAMBLE, HiddenKind.END_MATTER)
_CODES: dict[HiddenKind, int] = {
    kind: code for code, kind in enumerate(_COATS + _REGIONS, start=1)
}
_KINDS: dict[int, HiddenKind] = {code: kind for kind, code in _CODES.items()}
_RUN_RE = re.compile(rb"([\x01-\xff])\1*")


class _FileReader:
    """One file's passes. Each pass leaves what the next one needs."""

    def __init__(
        self,
        source: SourceFile,
        table: EnvironmentTable,
        referring: ReferenceTable = STANDARD_REFERENCES,
        order: ReadingOrder | None = None,
        regions: Mapping[tuple[str, str], Sequence[_Region]] | None = None,
    ) -> None:
        self.source = source
        self.text = source.text
        self.table = table
        self.referring = referring
        self.order = order
        #: A name declared more than once: the tables each of its uses
        #: reads under (`_in_force`).
        self.regions = regions or {}
        self.format = format_for(source.path, table)
        self.found = _Found()
        self.coats: dict[HiddenKind, list[tuple[int, int]]] = {
            kind: [] for kind in _COATS + _REGIONS
        }
        self.visible = self.text
        self.body = self.text
        self.paragraphs: list[tuple[int, int]] = []

    def span(self, start: int, end: int) -> Span:
        return Span(self.source.path, start, end)

    def read(self) -> _Found:
        if self.source.format is Format.LATEX:
            self._latex_masks()
        else:
            self._markdown_masks()
        self._title()
        if self.source.role in (FileRole.ROOT, FileRole.BODY):
            self._structure()
            self._sections()
            self._environments()
            self._displays()
            self._labels()
            self._references()
            self._citations()
            self._quotations()
            self._footnotes()
        self._macros()
        if self.source.role in (FileRole.ROOT, FileRole.BODY):
            self._definitions_in_the_body()
        self._paint()
        return self.found

    # --- Masks: what is not read, and why ---

    def _latex_masks(self) -> None:
        mask = read_mask(self.text)
        self.visible = mask.visible
        for start, end in mask.comments:
            self._comment_or_directive(start, end)
        for verbatim in mask.verbatims:
            if verbatim.name == COMMENT_ENV:
                self.coats[HiddenKind.COMMENT].append((verbatim.start, verbatim.end))
                self.found.comments.append(
                    Comment(
                        self.text[verbatim.body_start : verbatim.body_end],
                        self.span(verbatim.start, verbatim.end),
                        CommentKind.BLOCK,
                    )
                )
        for block in mask.disabled:
            self.coats[HiddenKind.DISABLED].append((block.start, block.end))
            self.found.comments.append(
                Comment(
                    self.text[block.start : block.end],
                    self.span(block.start, block.end),
                    CommentKind.DISABLED,
                )
            )
        role = self.source.role
        if role in (FileRole.PREAMBLE, FileRole.PACKAGE):
            self.coats[HiddenKind.PREAMBLE].append((0, len(self.text)))
            self.body = _blanked(self.visible, [(0, len(self.text))])
            return
        regions: list[tuple[int, int]] = []
        if role is FileRole.ROOT:
            begin = BEGIN_DOCUMENT_RE.search(self.visible)
            if begin is not None:
                regions.append((0, begin.end()))
                self.coats[HiddenKind.PREAMBLE].append((0, begin.end()))
                end = END_DOCUMENT_RE.search(self.visible, begin.end())
                if end is not None:
                    regions.append((end.start(), len(self.text)))
                    self.coats[HiddenKind.END_MATTER].append(
                        (end.start(), len(self.text))
                    )
        self.body = _blanked(self.visible, regions)

    def _comment_or_directive(self, start: int, end: int) -> None:
        text = self.text[start:end]
        match = TEX_DIRECTIVE_RE.match(text)
        if match is not None and _is_directive(match.group("name")):
            self.coats[HiddenKind.DIRECTIVE].append((start, end))
            self.found.directives.append(
                Directive(
                    match.group("name"),
                    match.group("body").strip(),
                    self.span(start, end),
                )
            )
            return
        self.coats[HiddenKind.COMMENT].append((start, end))
        self.found.comments.append(
            Comment(text[1:], self.span(start, end), CommentKind.LINE)
        )

    def _markdown_masks(self) -> None:
        blank: list[tuple[int, int]] = []
        header = yaml_header(self.text)
        if header is not None:
            start, end, title = header
            blank.append((start, end))
            if title is None:
                self.coats[HiddenKind.FRONT_MATTER].append((start, end))
            else:
                # Around the title, not over it: the title is rendered, and
                # a coat over it would be painted back inside it.
                self.coats[HiddenKind.FRONT_MATTER].append((start, title[0]))
                self.coats[HiddenKind.FRONT_MATTER].append((title[1], end))
                self.found.title = self.span(*title)
        lines = self.text.split("\n")
        starts = _line_starts(lines)
        fence_map = self.format.build_fence_map(lines)
        fences: list[tuple[int, int, bool]] = []
        opened: int | None = None
        for number, fenced in enumerate(fence_map):
            if fenced and opened is None:
                opened = number
            elif not fenced and opened is not None:
                fences.append((opened, number, True))
                opened = None
        if opened is not None:
            fences.append((opened, len(lines) - 1, False))
        for first, last, closed in fences:
            start, end = starts[first], starts[last] + len(lines[last])
            blank.append((start, end))
            self.found.environments.append(
                Environment(
                    name=CODE_FENCE,
                    meaning="verbatim",
                    kind=EnvKind.VERBATIM,
                    title=None,
                    span=self.span(start, end),
                    body=self.span(
                        min(starts[first] + len(lines[first]) + 1, end),
                        starts[last] if closed else end,
                    ),
                    closed=closed,
                )
            )
        outside = _blanked(self.text, blank)
        for match in _HTML_COMMENT_RE.finditer(outside):
            start, end = match.span()
            blank.append((start, end))
            directive = DIRECTIVE_RE.fullmatch(match.group(0))
            if directive is not None and _is_directive(directive.group("name")):
                self.coats[HiddenKind.DIRECTIVE].append((start, end))
                self.found.directives.append(
                    Directive(
                        directive.group("name"),
                        directive.group("body").strip(),
                        self.span(start, end),
                    )
                )
                continue
            self.coats[HiddenKind.COMMENT].append((start, end))
            self.found.comments.append(
                Comment(match.group(1), self.span(start, end), CommentKind.HTML)
            )
        self.visible = _blanked(self.text, blank)
        self.body = self.visible

    # --- The title and the front matter ---

    def _title(self) -> None:
        if self.source.format is not Format.LATEX:
            return
        if self.source.role is FileRole.PACKAGE:
            return
        for match in _FRONT_MATTER_RE.finditer(self.visible):
            end = braced_end(self.visible, match.end())
            if end is not None:
                self.coats[HiddenKind.FRONT_MATTER].append((match.start(), end))
        match = _TITLE_RE.search(self.visible)
        if match is None:
            return
        end = braced_end(self.visible, match.end())
        if end is None:
            return
        self.coats[HiddenKind.STRUCTURE].append((match.start(), match.end() + 1))
        self.coats[HiddenKind.STRUCTURE].append((end - 1, end))
        self.coats[HiddenKind.STRUCTURE].extend(
            _layout_lines(self.visible, match.end() + 1, end - 1)
        )
        self.found.title = self.span(match.end() + 1, end - 1)

    # --- Structure: what assembles the document and prints nothing ---

    def _structure(self) -> None:
        structure = self.coats[HiddenKind.STRUCTURE]
        structure.extend(match.span() for match in LABEL_RE.finditer(self.body))
        structure.extend(match.span() for match in STRUCTURE_RE.finditer(self.body))
        structure.extend(_layout_lines(self.body, 0, len(self.body)))

    # --- Sections ---

    def _sections(self) -> None:
        lines = self.body.split("\n")
        starts = _line_starts(lines)
        for index, line in enumerate(lines):
            level = self.format.heading_level(line)
            if level is None:
                continue
            start = starts[index] + len(line) - len(line.lstrip())
            end_line, end_column = self.format.heading_end(lines, index)
            end = starts[end_line] + end_column
            attributes = self.format.heading_attributes(line)
            pandoc_id = None
            if attributes is not None:
                first, last, pandoc_id, _ = attributes
                self.coats[HiddenKind.STRUCTURE].append(
                    (starts[index] + first, starts[index] + last)
                )
                if pandoc_id is not None:
                    at = self.body.index("#" + pandoc_id, starts[index] + first) + 1
                    self.found.labels.append(
                        Label(pandoc_id, self.span(at, at + len(pandoc_id)))
                    )
            self.found.sections.append(
                Section(
                    level=level,
                    title=self.format.heading_title_at(lines, index),
                    label=self._heading_label(start, end) or pandoc_id,
                    heading=self.span(start, end),
                    starred=self.format.heading_starred(line),
                )
            )

    def _heading_label(self, start: int, end: int) -> str | None:
        """A `\\label` inside the heading, or the first thing after it."""
        inside = LABEL_RE.search(self.body, start, end)
        if inside is not None:
            return inside.group(1)
        after = end + len(self.body[end:]) - len(self.body[end:].lstrip())
        following = LABEL_RE.match(self.body, after)
        return following.group(1) if following is not None else None

    # --- Environments ---

    def _environments(self) -> None:
        lines = self.body.split("\n")
        starts = _line_starts(lines)
        for match in ENV_BEGIN_RE.finditer(self.body):
            name = match.group(1)
            line = bisect.bisect_right(starts, match.start()) - 1
            column = match.start() - starts[line]
            closing = self.format.find_env_end(lines, line, name, None, column)
            end = starts[closing.line] + closing.column
            body_end = end - len(f"\\end{{{name}}}") if closing.closed else end
            table = self._region(ENVIRONMENT, name, match.start()).table
            kind = self._kind(name, table)
            meaning = self._meaning(name, kind, table)
            title = None
            body_start = match.end()
            bracket = _bracketed_end(self.body, match.end())
            restatable = (
                _restatable(self.body, match.end()) if name in RESTATABLE_ENVS else None
            )
            prefix = table.boxed_prefix(name)
            boxed = (
                _boxed(self.body, match.end(), name.endswith("*"))
                if prefix is not None
                else None
            )
            if restatable is not None:
                title, body_start = restatable.title, restatable.body_start
                meaning = self._region(
                    ENVIRONMENT, restatable.base, match.start()
                ).table.meaning(restatable.base)
                self.found.labels.append(
                    Label(restatable.command, self.span(*restatable.command_at))
                )
            elif boxed is not None:
                title, body_start = boxed.title, boxed.body_start
                if boxed.label is not None:
                    label, at = boxed.label
                    self.found.labels.append(Label(f"{prefix}:{label}", self.span(*at)))
            elif bracket is not None:
                body_start = bracket
                if kind in (EnvKind.STATEMENT, EnvKind.PROOF):
                    title = self.body[match.end() + 1 : bracket - 1].strip()
            self.found.environments.append(
                Environment(
                    name=name,
                    meaning=meaning,
                    kind=kind,
                    title=title,
                    span=self.span(match.start(), end),
                    body=self.span(min(body_start, body_end), body_end),
                    closed=closing.closed,
                )
            )

    def _region(self, namespace: str, name: str, at: int) -> _Region:
        """The tables `name`, used at `at`, reads under: its region's where
        the paper declares it more than once, else the file's."""
        found = self.regions.get((namespace, name))
        if not found or self.order is None:
            return _Region((0, 0, 0), self.table, self.referring)
        key = self.order.key(self.span(at, at))
        return next((r for r in reversed(found) if r.at <= key), found[0])

    def _kind(self, name: str, table: EnvironmentTable) -> EnvKind:
        if name in table.names:
            return EnvKind.STATEMENT
        kind = static_kind(name)
        if kind is not None:
            return kind
        if name in table.containers:
            return EnvKind.CONTAINER
        if name in table.lists:
            return EnvKind.LIST
        inner = table.wrapped(name)
        if inner is not None:
            return static_kind(inner) or EnvKind.OTHER
        return EnvKind.OTHER

    def _meaning(self, name: str, kind: EnvKind, table: EnvironmentTable) -> str | None:
        if kind is EnvKind.STATEMENT:
            return table.meaning(name)
        if kind is EnvKind.OTHER:
            return None
        return (table.wrapped(name) or name).rstrip("*")

    def _displays(self) -> None:
        """`\\[ … \\]` and `$$ … $$`, as environments of kind display."""
        for start, end, closed in _bracket_displays(self.body):
            self._display(BRACKET_DISPLAY, start, end, closed)
        for start, end, closed in _dollar_displays(self.body):
            self._display(DOLLAR_DISPLAY, start, end, closed)

    def _display(self, name: str, start: int, end: int, closed: bool) -> None:
        width = len(name)
        self.found.environments.append(
            Environment(
                name=name,
                meaning="displaymath",
                kind=EnvKind.DISPLAY,
                title=None,
                span=self.span(start, end),
                body=self.span(start + width, end - width if closed else end),
                closed=closed,
            )
        )

    # --- Labels, references, citations, footnotes ---

    def _labels(self) -> None:
        """Each `\\label`, each label a mapped command defines, and the
        command a `restatable` declares (read with its environment)."""
        self.found.labels.extend(
            Label(match.group(1), self.span(*match.span()))
            for match in LABEL_RE.finditer(self.body)
        )
        for referring, keep in self._readings():
            mapped = referring.label_pattern()
            if mapped is not None:
                self.found.labels.extend(
                    Label(match.group(1).strip(), self.span(*match.span()))
                    for match in mapped.finditer(self.body)
                    if keep(_command_at(self.body, match.start()), match.start())
                )
        self.found.labels.sort(key=lambda label: label.at.start)

    def _readings(self):  # type: ignore[no-untyped-def]
        """Each reference table a command is read under, with which of its
        finds to keep (`(command, start) -> bool`): the file's, for every
        command not declared more than once; each region's, for such a
        command where that region is in force."""
        several = {name for namespace, name in self.regions if namespace == COMMAND}
        yield self.referring, lambda command, at: command not in several
        for (namespace, name), found in self.regions.items():
            if namespace != COMMAND:
                continue
            for region in found:
                yield (
                    region.referring,
                    lambda command, at, name=name, region=region: (
                        command == name and self._region(COMMAND, name, at) is region
                    ),
                )

    def _references(self) -> None:
        for referring, keep in self._readings():
            self._references_under(referring, keep)
        self.found.references.sort(key=lambda reference: reference.at.start)

    def _references_under(self, referring: ReferenceTable, keep) -> None:  # type: ignore[no-untyped-def]
        for found in references(self.body, referring):
            if not keep(found.command, found.start):
                continue
            command, word = found.command, found.word or None
            if command == "S\\ref":
                command, word = "ref", "\\S"
            self.found.references.append(
                Reference(
                    command, word, found.targets, self.span(found.start, found.end)
                )
            )

    def _citations(self) -> None:
        for match in CITE_RE.finditer(self.body):
            self.found.citations.append(
                Citation(
                    command=match.group(1),
                    keys=split_list(match.group(3)),
                    locator=_last_optional(match.group(2)),
                    at=self.span(*match.span()),
                )
            )

    def _quotations(self) -> None:
        """Quotation sites in their three shapes: an
        environment of `QUOTE_ENVS` with a citation inside it or in the
        sentence after it; an `\\enquote` or `\\textquote` with one in its
        sentence; a span in double quotation marks (straight, curly or
        LaTeX's) of `QUOTE_MIN_WORDS` words or more, with one in its
        sentence."""
        self.paragraphs = _paragraphs(self.body, self.format.heading_level)
        for env in self.found.environments:
            if env.name not in QUOTE_ENVS or not env.closed:
                continue
            body = env.body
            inside = self._citations_within(body.start, body.end)
            after = _skip_space(self.body, env.span.end)
            follows = after < len(self.body) and not _paragraph_break(
                self.body[env.span.end : after]
            )
            cited = inside or (self._citations_in_sentence(after) if follows else [])
            self._quoted(body.start, body.end, cited)
        for match in ENQUOTE_RE.finditer(self.body):
            opening = _bracketed_skip(self.body, match.end())
            if opening is None:
                continue
            end = braced_end(self.body, opening)
            if end is None:
                continue
            self._quoted(
                opening + 1, end - 1, self._citations_in_sentence(match.start())
            )
        for start, end in self.paragraphs:
            for inner, close in _double_quoted(self.body, start, end):
                if len(self.body[inner:close].split()) < QUOTE_MIN_WORDS:
                    continue
                self._quoted(inner, close, self._citations_in_sentence(inner))

    def _citations_within(self, start: int, end: int) -> list[Citation]:
        return [c for c in self.found.citations if start <= c.at.start < end]

    def _citations_in_sentence(self, offset: int) -> list[Citation]:
        """The citations in the sentence covering `offset`, read within its
        paragraph."""
        for start, end in self.paragraphs:
            if start <= offset < end:
                bounds = sentence_at(self.body[start:end], offset - start)
                if bounds is None:
                    return []
                return self._citations_within(start + bounds[0], start + bounds[1])
        return []

    def _quoted(self, start: int, end: int, cited: Sequence[Citation]) -> None:
        """One quotation per citation of `self.body[start:end]`, its ends
        trimmed; its text without the citations inside it (attribution
        apparatus, not quoted words)."""
        raw = self.body[start:end]
        start += len(raw) - len(raw.lstrip())
        end -= len(raw) - len(raw.rstrip())
        text = CITE_RE.sub("", self.body[start:end]).strip()
        if not text:
            return
        for citation in cited:
            found = Quotation(
                self.span(start, end),
                text,
                citation.at,
                citation.keys,
                citation.locator,
            )
            if found not in self.found.quotations:
                self.found.quotations.append(found)

    def _footnotes(self) -> None:
        for match in _FOOTNOTE_RE.finditer(self.body):
            end = braced_end(self.body, match.end())
            if end is None:
                continue
            self.found.footnotes.append(
                Footnote(
                    at=self.span(match.start(), end),
                    text=self.span(match.end() + 1, end - 1),
                )
            )

    # --- Macros ---

    def _macros(self) -> None:
        if self.source.format is not Format.LATEX and "\\" not in self.visible:
            return
        self.found.macros.extend(macros_in(self.source.path, self.visible))

    def _definitions_in_the_body(self) -> None:
        """A macro defined in the body prints nothing: it is structure."""
        for macro in self.found.macros:
            if self.body[macro.at.start : macro.at.end].strip():
                self.coats[HiddenKind.STRUCTURE].append((macro.at.start, macro.at.end))

    # --- The hidden map ---

    def _paint(self) -> None:
        codes = bytearray(len(self.text))
        for kind in _COATS + _REGIONS:
            _coat(codes, self.coats[kind], _CODES[kind])
        title = self.found.title
        if title is not None:
            codes[title.start : title.end] = bytes(title.end - title.start)
            for kind in _COATS:
                clipped = [
                    (max(start, title.start), min(end, title.end))
                    for start, end in self.coats[kind]
                    if start < title.end and end > title.start
                ]
                _coat(codes, clipped, _CODES[kind])
        self.found.hidden = tuple(
            Hidden(self.span(*run.span()), _KINDS[run.group(1)[0]])
            for run in _RUN_RE.finditer(bytes(codes))
        )


def _layout_lines(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The layout commands of each line of `text[start:end]` that holds
    nothing else but labels and structure."""
    found: list[tuple[int, int]] = []
    offset = start
    for line in text[start:end].split("\n"):
        layout = list(_LAYOUT_RE.finditer(line))
        if layout:
            bare = _LAYOUT_RE.sub("", STRUCTURE_RE.sub("", LABEL_RE.sub("", line)))
            if not bare.strip():
                found.extend(
                    (offset + match.start(), offset + match.end()) for match in layout
                )
        offset += len(line) + 1
    return found


def _coat(codes: bytearray, ranges: Sequence[tuple[int, int]], code: int) -> None:
    for start, end in ranges:
        if end > start:
            codes[start:end] = bytes([code]) * (end - start)


def _is_directive(name: str) -> bool:
    return name in {kind.value for kind in DirectiveKind}


def _blanked(text: str, ranges: Iterable[tuple[int, int]]) -> str:
    """`text` with spaces over each range, newlines kept."""
    chars = list(text)
    for start, end in ranges:
        for index in range(start, end):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def _line_starts(lines: Sequence[str]) -> list[int]:
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    return starts


def _bracketed_end(text: str, start: int) -> int | None:
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


def _escaped(text: str, index: int) -> bool:
    """Whether an odd run of backslashes stands before `text[index]`."""
    slashes = 0
    while index - slashes - 1 >= 0 and text[index - slashes - 1] == "\\":
        slashes += 1
    return slashes % 2 == 1


def _bracket_displays(text: str) -> list[tuple[int, int, bool]]:
    """`\\[ … \\]` pairs. `\\\\[2pt]` is a line break, not a display."""
    found: list[tuple[int, int, bool]] = []
    position = 0
    while True:
        start = text.find("\\[", position)
        while start >= 0 and _escaped(text, start):
            start = text.find("\\[", start + 1)
        if start < 0:
            return found
        end = text.find("\\]", start + 2)
        while end >= 0 and _escaped(text, end):
            end = text.find("\\]", end + 1)
        if end < 0:
            found.append((start, len(text), False))
            return found
        found.append((start, end + 2, True))
        position = end + 2


def _dollar_displays(text: str) -> list[tuple[int, int, bool]]:
    """`$$ … $$` pairs, in order. An escaped `\\$` is a dollar sign."""
    marks = [
        index
        for index in range(len(text) - 1)
        if text.startswith("$$", index) and not _escaped(text, index)
    ]
    # A run of three or more dollars is read from its left.
    kept: list[int] = []
    for index in marks:
        if kept and index < kept[-1] + 2:
            continue
        kept.append(index)
    found = [(kept[i], kept[i + 1] + 2, True) for i in range(0, len(kept) - 1, 2)]
    if len(kept) % 2:
        found.append((kept[-1], len(text), False))
    return found


def _paragraph_break(text: str) -> bool:
    """Whether a run of white space holds a blank line."""
    return text.count("\n") >= 2


def _paragraphs(text: str, heading_level) -> list[tuple[int, int]]:
    """The runs of prose between blank lines, a heading's line its own,
    as offset ranges."""
    found: list[tuple[int, int]] = []
    start: int | None = None
    position = 0
    for line in text.split("\n"):
        end = position + len(line)
        if not line.strip() or heading_level(line) is not None:
            if start is not None:
                found.append((start, position - 1))
            start = None
        elif start is None:
            start = position
        position = end + 1
    if start is not None:
        found.append((start, len(text)))
    return found


def _double_quoted(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The spans between double quotation marks,
    straight, curly or LaTeX's ``…'', within `text[start:end]`, as offset
    ranges of what lies between the marks."""
    pairs = {"\u201c": "\u201d", '"': '"', "``": "''"}
    spans: list[tuple[int, int]] = []
    index = start
    while index < end:
        opening = next(
            (o for o in ("``", "\u201c", '"') if text.startswith(o, index)), None
        )
        if opening is None:
            index += 1
            continue
        inner = index + len(opening)
        close = text.find(pairs[opening], inner, end)
        if close < 0:
            break
        spans.append((inner, close))
        index = close + len(pairs[opening])
    return spans


def _bracketed_skip(text: str, index: int) -> int | None:
    """The offset of the `{` of a macro's first
    braced argument, its optional `[...]` groups passed over."""
    while index < len(text) and text[index] == "[":
        close = text.find("]", index)
        if close < 0:
            return None
        index = close + 1
    return index if index < len(text) and text[index] == "{" else None


def _last_optional(groups: str) -> str | None:
    """The postnote of a biblatex cite, the last `[...]` group."""
    found = re.findall(r"\[([^\]]*)\]", groups)
    return found[-1].strip() if found and found[-1].strip() else None


def macros_in(path: str, text: str) -> list[Macro]:
    """Every definition `text` makes, recorded and never expanded; `text` is
    the file at `path` with what cannot define blanked."""
    found: list[Macro] = []
    for match in _MACRO_RE.finditer(text):
        macro = _macro(text, match)
        if macro is not None:
            command, name, arguments, definition, end = macro
            found.append(
                Macro(
                    command, name, arguments, definition, Span(path, match.start(), end)
                )
            )
    return found


def _macro(
    text: str, match: re.Match[str]
) -> tuple[str, str, int | None, str, int] | None:
    """One definition: `(command, name, arguments, definition, end)`, or
    `None` when it cannot be read without expanding something."""
    command = match.group(1)
    index = _skip_space(text, match.end())
    if command in _DEF_COMMANDS:
        name = _CONTROL_SEQUENCE_RE.match(text, index)
        if name is None:
            return None
        brace = text.find("{", name.end())
        if brace < 0 or "\n\n" in text[name.end() : brace]:
            return None
        parameters = text[name.end() : brace]
        end = braced_end(text, brace)
        if end is None:
            return None
        return (
            command,
            name.group(0),
            len(_PARAMETER_RE.findall(parameters)),
            text[brace:end],
            end,
        )
    if command in _ENVIRONMENT_COMMANDS:
        if index >= len(text) or text[index] != "{":
            return None
        name_end = braced_end(text, index)
        if name_end is None:
            return None
        name = text[index + 1 : name_end - 1].strip()
        index = name_end
    elif index < len(text) and text[index] == "{":
        name_end = braced_end(text, index)
        if name_end is None:
            return None
        name = text[index + 1 : name_end - 1].strip()
        index = name_end
    else:
        found = _CONTROL_SEQUENCE_RE.match(text, index)
        if found is None:
            return None
        name, index = found.group(0), found.end()
    arguments: int | None = 0
    index = _skip_space(text, index)
    count = _bracketed_end(text, index)
    if count is not None and command != "DeclareMathOperator":
        written = text[index + 1 : count - 1].strip()
        arguments = int(written) if written.isdigit() else None
        index = _skip_space(text, count)
        default = _bracketed_end(text, index)
        if default is not None:
            index = _skip_space(text, default)
    groups = 2 if command in _ENVIRONMENT_COMMANDS else 1
    start = index
    for _ in range(groups):
        index = _skip_space(text, index)
        if index >= len(text) or text[index] != "{":
            return None
        end = braced_end(text, index)
        if end is None:
            return None
        index = end
    return (command, name, arguments, text[_skip_space(text, start) : index], index)


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index
