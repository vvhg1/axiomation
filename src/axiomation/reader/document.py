"""The document layer: what a paper's files say, before any block is cut.

The cutter reads nothing else. Every item carries a `Span`, a character range
in one file's text. Every tuple on `Document` is in reading order: the order
LaTeX reads, where a body include is read where it stands.

What is not rendered is listed per file as `Hidden` spans. Everything else
is rendered text, which the cutter's blocks must cover.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class Format(StrEnum):
    """The syntax a file is read in, chosen by its extension."""

    LATEX = "latex"
    MARKDOWN = "markdown"


@dataclass(frozen=True, slots=True, order=True)
class Span:
    """Characters `start` to `end` of one file's text, end exclusive.

    `file` is POSIX, relative to the paper directory.
    """

    file: str
    start: int
    end: int


class FileRole(StrEnum):
    """How the include walk reached a file."""

    ROOT = "root"
    #: Reached by an include in the body: read for everything.
    BODY = "body"
    #: Reached by an include in a preamble: declarations and macros only.
    PREAMBLE = "preamble"
    #: A `.sty` or `.cls` shipped beside the paper, reached by `\usepackage`
    #: or `\documentclass`.
    PACKAGE = "package"


class HiddenKind(StrEnum):
    """Why a run of characters is not rendered."""

    PREAMBLE = "preamble"
    END_MATTER = "end-matter"
    #: Authors, dates, affiliations: metadata, not text.
    FRONT_MATTER = "front-matter"
    COMMENT = "comment"
    DIRECTIVE = "directive"
    #: An `\iffalse` block.
    DISABLED = "disabled"
    #: Markup that assembles the document and prints nothing of its own.
    STRUCTURE = "structure"


@dataclass(frozen=True, slots=True)
class Hidden:
    span: Span
    kind: HiddenKind


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One file the walk read. `text` is what every span indexes."""

    path: str
    role: FileRole
    format: Format
    sha256: str
    text: str
    hidden: tuple[Hidden, ...]


@dataclass(frozen=True, slots=True)
class Include:
    """One command that pulls a file in. `resolved` is `None` when no file
    matches, or the path is built from a macro."""

    command: str
    target: str
    at: Span
    resolved: str | None
    role: FileRole


class EnvKind(StrEnum):
    #: Every name in the environment table, declared ones included.
    STATEMENT = "statement"
    PROOF = "proof"
    LIST = "list"
    DISPLAY = "display"
    FLOAT = "float"
    CONTAINER = "container"
    VERBATIM = "verbatim"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Declaration:
    """One environment a paper declares, and what the reader reads it as:
    a statement and the standard environment it `means` (or none); a frame,
    `container`, meaning none; or, for an environment that wraps one the
    reader knows by name, that one's kind, `meaning` its name. `printed` is
    the name it prints, empty for a frame."""

    name: str
    printed: str
    meaning: str | None
    at: Span
    kind: EnvKind = EnvKind.STATEMENT
    #: A tcolorbox theorem's label prefix: a use's label argument `x`
    #: defines `<prefix>:x`. `None` for any other declaration.
    label_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class Environment:
    """`\\begin{name}` to the end of its `\\end{name}`, or a display or a code
    fence. `body` is what lies between the opening (with its `[title]`) and
    the closing. `closed` is false when nothing closes it: the span then runs
    to the end of the file."""

    name: str
    meaning: str | None
    kind: EnvKind
    title: str | None
    span: Span
    body: Span
    closed: bool


@dataclass(frozen=True, slots=True)
class Section:
    """A heading. `heading` spans the whole heading command or line."""

    level: int
    title: str
    label: str | None
    heading: Span
    starred: bool


@dataclass(frozen=True, slots=True)
class Label:
    name: str
    at: Span


@dataclass(frozen=True, slots=True)
class Reference:
    """One reference command. A cleveref list or range names several
    targets; `word` is the word before it (`Theorem~`), or `\\S`."""

    command: str
    word: str | None
    targets: tuple[str, ...]
    at: Span


@dataclass(frozen=True, slots=True)
class Citation:
    command: str
    keys: tuple[str, ...]
    locator: str | None
    at: Span


@dataclass(frozen=True, slots=True)
class Quotation:
    """Words the paper quotes from a cited work (a quotation site): a
    `quote`, `quotation` or `displayquote` environment, an `\\enquote` or
    `\\textquote`, or a span in double quotation marks of at least eight
    words, with a citation inside it or in its sentence. `at` the quoted
    words, their marks outside it; `text` those words, any citation inside
    them removed; `citation` the citation naming the work,
    one quotation per citation."""

    at: Span
    text: str
    citation: Span
    keys: tuple[str, ...]
    locator: str | None


@dataclass(frozen=True, slots=True)
class Footnote:
    """The whole `\\footnote{…}`, and its argument."""

    at: Span
    text: Span


@dataclass(frozen=True, slots=True)
class Macro:
    """A definition, recorded and never expanded. `definition` is the source
    after the name and the argument count, as written."""

    command: str
    name: str
    arguments: int | None
    definition: str
    at: Span


class VocabularyKind(StrEnum):
    """What a name the reader's tables do not know is for, as a paper's
    vocabulary map says."""

    #: An environment that bounds a statement.
    STATEMENT = "statement"
    #: An environment that only wraps its body, a frame or a box: the body
    #: is read as if it stood alone.
    CONTAINER = "container"
    #: An environment that lists `\\item`s.
    LIST = "list"
    #: An environment that wraps one the reader knows, and is read as it:
    #: `\\newenvironment{proofsketch}{\\begin{proof}[Sketch]}{\\end{proof}}`.
    WRAPS = "wraps"
    #: A command that points at labels.
    REFERENCE = "reference"
    #: A command that defines a label, as `\\label` does.
    LABEL = "label"
    #: Nothing the reader reads: the name stays as it was.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class EnvEntry:
    """One environment in a paper's vocabulary map. `base` is the standard
    environment a statement means, the list it is built on, or the
    environment it wraps; `None` when it means none (`example` bounds a
    statement and means nothing)."""

    name: str
    kind: VocabularyKind
    base: str | None = None


@dataclass(frozen=True, slots=True)
class RefEntry:
    """One reference command in a paper's vocabulary map. `takes_list`: its
    argument is a comma list of labels; `prefix`: what it puts before its
    argument to make the label (`\\lemref{main}` points at `lem:main`);
    `word`: what it prints before the number (`Lemma`)."""

    command: str
    takes_list: bool = False
    prefix: str = ""
    word: str | None = None


#: A map answer's namespaces: an environment's name, a command's.
ENVIRONMENT = "environment"
COMMAND = "command"


@dataclass(frozen=True, slots=True)
class MapAnswer:
    """One candidate as a vocabulary map answers it: its namespace
    (`ENVIRONMENT` or `COMMAND`), its name, the declaring text it was
    answered under (`None` for a name the paper never declares), its kind
    as written (`reference list prefix=lem:`), and the paper's words it
    rests on. An answer holds while its candidate's declaring text is the
    same: the map is carried from version to version, and a name whose
    declaration changed is asked again. A name the paper declares more
    than once has an answer for each declaring text, read where that
    declaration is in force."""

    namespace: str
    name: str
    declaration: str | None
    kind: str
    evidence: str
    #: Who gave it, where a person or an agent did (`person`, `agent`);
    #: `None` for a model's. A person's answer holds against an agent's
    #: edit while it is carried.
    by: str | None = None

    @property
    def place(self) -> tuple[str, str, str | None]:
        return (self.namespace, self.name, self.declaration)

    @property
    def words(self) -> tuple[str, ...]:
        return tuple(self.kind.split()[1:])

    @property
    def kind_of(self) -> VocabularyKind:
        return VocabularyKind(self.kind.split()[0])


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """A paper's own names the reader reads as it reads its tables, each
    entry an answer to one candidate. A name a table already knows, a
    declaration included, keeps what the table says. A map's `answers`
    are what its reading fields were made from (`of`), a command's `none`
    included, which reads nothing; a map made by code has none."""

    environments: tuple[EnvEntry, ...] = ()
    references: tuple[RefEntry, ...] = ()
    #: The commands that define a label, its name their first argument.
    labels: tuple[str, ...] = ()
    answers: tuple[MapAnswer, ...] = ()

    @classmethod
    def of(cls, answers: Sequence[MapAnswer]) -> Vocabulary:
        """The map the answers give: each environment and command by its
        kind; an environment's `none` adds an entry, so it is read as
        answered."""
        environments = []
        references = []
        labels = []
        for answer in answers:
            kind, words = answer.kind_of, answer.words
            if answer.namespace == ENVIRONMENT:
                environments.append(
                    EnvEntry(answer.name, kind, words[0] if words else None)
                )
            elif kind is VocabularyKind.REFERENCE:
                options = dict(word.split("=", 1) for word in words if "=" in word)
                references.append(
                    RefEntry(
                        answer.name,
                        "list" in words,
                        options.get("prefix", ""),
                        options.get("word"),
                    )
                )
            elif kind is VocabularyKind.LABEL:
                labels.append(answer.name)
        return cls(
            tuple(environments), tuple(references), tuple(labels), tuple(answers)
        )

    def answer(
        self, namespace: str, name: str, declaration: str | None
    ) -> MapAnswer | None:
        """The answer to `name` under the declaring text `declaration`."""
        return next(
            (a for a in self.answers if a.place == (namespace, name, declaration)),
            None,
        )


class CandidateShape(StrEnum):
    """Why code lists a name for the vocabulary map."""

    #: An environment no table knows, used outside a display, a float and
    #: a listing, or declared by `\\newenvironment`.
    ENVIRONMENT = "environment"
    #: A command the paper defines whose body uses a reference command.
    MACRO = "macro"
    #: A command no table knows whose argument is one of the paper's labels.
    USE = "use"
    #: A command around a target a reference names and no `\\label` defines.
    LABEL_DEFINER = "label-definer"
    #: Another definition of a command listed for one of its definitions,
    #: this one with no reference in its body: what the name reads as where
    #: this definition is in force.
    REDEFINED = "redefined"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One name the vocabulary map must answer. `uses` are its first uses
    (at most three), `declaration` the command that declares it, if code
    found one. A name declared more than once with different texts is a
    candidate for each, `index` of `count` in reading order, its uses
    those where that declaration is in force (the last one before them)."""

    name: str
    shape: CandidateShape
    uses: tuple[Span, ...]
    declaration: Span | None
    index: int = 0
    count: int = 1


class CommentKind(StrEnum):
    #: A LaTeX `%` comment.
    LINE = "line"
    #: A Markdown `<!-- … -->` that is not a directive.
    HTML = "html"
    #: The `comment` environment.
    BLOCK = "block"
    #: An `\iffalse` block.
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class Comment:
    text: str
    at: Span
    kind: CommentKind


@dataclass(frozen=True, slots=True)
class Directive:
    """One of the author's directives, in the file's own spelling."""

    name: str
    body: str
    at: Span


@dataclass(frozen=True, slots=True)
class Document:
    """A paper's document layer. Every tuple is in reading order."""

    format: Format
    root: str
    files: tuple[SourceFile, ...]
    includes: tuple[Include, ...]
    never_included: tuple[str, ...]
    declarations: tuple[Declaration, ...]
    title: Span | None
    sections: tuple[Section, ...]
    environments: tuple[Environment, ...]
    labels: tuple[Label, ...]
    references: tuple[Reference, ...]
    citations: tuple[Citation, ...]
    quotations: tuple[Quotation, ...]
    footnotes: tuple[Footnote, ...]
    macros: tuple[Macro, ...]
    comments: tuple[Comment, ...]
    directives: tuple[Directive, ...]
    #: The vocabulary map the layer was read with.
    vocabulary: Vocabulary = Vocabulary()
    #: The version a kept state names, where the layer is that state's text:
    #: read from its kept files, or from a paper today's reader reads alike.
    #: A reader change since moves `files_hash`, never the version the text
    #: was kept as (`docs/model.md` § 7).
    version: str | None = None

    def file(self, path: str) -> SourceFile:
        """The file read at `path`."""
        for source in self.files:
            if source.path == path:
                return source
        raise KeyError(path)

    def text(self, span: Span) -> str:
        """The characters a span covers."""
        return self.file(span.file).text[span.start : span.end]


def document_hash(document: Document) -> str:
    """A version's identity: the kept state's where the layer is a kept
    state's text (`Document.version`), else `files_hash`."""
    return document.version or files_hash(document)


def files_hash(document: Document) -> str:
    """The files read, hashed: sha256 over them in reading order, each its
    path and its text, so two versions with one text and one include order
    hash the same."""
    digest = hashlib.sha256()
    for source in document.files:
        for part in (source.path, source.text):
            data = part.encode("utf-8")
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    return digest.hexdigest()
