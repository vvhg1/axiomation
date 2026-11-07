"""The names a paper's vocabulary map must answer.

The reader's tables know the standard references and statement
environments, the frames every paper means the same way, and what a paper
declares in the spellings `declarations.py` reads: its theorems, frames,
wrappers and reference commands, and its packages'. What a paper or a
package names
in other words, code cannot tell the purpose of; it can find every such
name, and that is what this module does. A model answers what each one is
for (`docs/architecture.md` § 2), and the reader reads the answer as it
reads its tables (`Vocabulary`).

A name is a candidate when:

- **an environment** no table knows is used outside a display, a float
  and a listing (the cutter never looks inside those, so nothing there
  can change a cut), or `\\newenvironment` declares one (a
  `\\renewenvironment` changes one LaTeX already has);
- **a macro** the paper defines uses a reference command in its body
  (`\\newcommand{\\lemref}[1]{Lemma~\\ref{lem:#1}}`);
- **a use**: a command no table knows takes, in every use, one of the
  paper's labels as its argument, a comma list of them, or a label less a
  prefix the paper's labels use (`\\lemmaref{main}` for `lem:main`): a
  reference always points at a label, so this finds a package's reference
  commands without knowing the package, and a command that also takes
  other words (`\\emph{confidence set}` beside a label of that name) is
  not one;
- **a label definer** stands around a target a reference names and no
  `\\label` defines.

Each name is listed once, environments and commands apart, and a name the
map already answers is not listed again while its declaration stands. A
name declared more than once with different texts (a `\\renewcommand`, a
`\\renewenvironment`, in any file) is listed once for each, with the uses
where that declaration is in force: the last one before them in reading
order, the first for a use before any (`in_force`). The reader reads each
use so (`layer.read_layer`), and an edit of any one declaration asks that
one again.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence

from axiomation.reader.declarations import (
    argument_groups,
    declaring_text,
    reference_table,
)
from axiomation.reader.document import (
    COMMAND,
    ENVIRONMENT,
    Candidate,
    CandidateShape,
    Document,
    EnvKind,
    FileRole,
    Format,
    Macro,
    SourceFile,
    Span,
    Vocabulary,
)
from axiomation.reader.environments import static_kind
from axiomation.reader.formats.md_latex import (
    RANGE_COMMANDS,
    STANDARD_REFERENCES,
    STATEMENT_ENVS,
    references,
)
from axiomation.reader.layer import ReadingOrder, macros_in

#: A candidate's uses shown as its evidence.
USES_SHOWN = 3
#: Environments the cutter reads as one unit: nothing inside one bounds a
#: block.
_UNITS = frozenset({EnvKind.DISPLAY, EnvKind.FLOAT, EnvKind.VERBATIM})
_ENVIRONMENT_COMMANDS = frozenset({"newenvironment", "renewenvironment"})
#: A command with optional groups and one braced argument.
_COMMAND_RE = re.compile(r"\\([A-Za-z@]+)\*?(?:\[[^\]\n]*\])*\{([^{}\n]*)\}")
#: A command's name, at the start of its text.
_COMMAND_NAME = re.compile(r"\\([A-Za-z@]+)")
#: The commands that define what an environment is, counted among its
#: declarations beside `\\newenvironment`: a `\\newcounter{example}`, a
#: `\\surroundwithmdframed{summarybox}` (which frames one already defined) or a
#: `\\label{example}` declares nothing.
_DECLARING = re.compile(
    r"(?:re)?newenvironment|provideenvironment"
    r"|(?:New|Renew|Provide|Declare)DocumentEnvironment|(?:New|Renew)Environ"
    r"|newtheorem|declaretheorem|newtcbtheorem|newmdtheoremenv"
    r"|(?:re)?newmdenv|(?:re)?newtcolorbox|(?:New|Renew|Declare)TColorBox"
    r"|newtcblisting|lstnewenvironment|(?:re)?newlist"
    r"|newfloat|DeclareFloatingEnvironment"
)
#: Commands that take a label and are not a reference: `\\label` defines
#: it, `\\pageref` prints a page, `\\begin` and `\\end` name environments.
_NOT_REFERENCES = frozenset({"label", "pageref", "begin", "end"})


def candidates(
    document: Document, vocabulary: Vocabulary | None = None
) -> tuple[Candidate, ...]:
    """Every name the paper's tables and its vocabulary map do not know, in
    the order its shapes are listed above, each shape in reading order. A
    map's answer covers its name while the name's declaring text is the
    one it was answered under; a name a map answers by its reading fields
    alone (one code made) is covered by its name."""
    answered: set[tuple[str, str, str | None]] = set()
    named_envs: set[str] = set()
    named_commands: set[str] = set()
    if vocabulary is not None:
        answered = {a.place for a in vocabulary.answers}
        names = {(a.namespace, a.name) for a in vocabulary.answers}
        named_envs = {
            e.name
            for e in vocabulary.environments
            if (ENVIRONMENT, e.name) not in names
        }
        named_commands = {
            name
            for name in (
                *(r.command for r in vocabulary.references),
                *vocabulary.labels,
            )
            if (COMMAND, name) not in names
        }

    def covered(namespace: str, candidate: Candidate) -> bool:
        declared = (
            document.text(candidate.declaration) if candidate.declaration else None
        )
        return (namespace, candidate.name, declared) in answered

    order = ReadingOrder.of(document)
    found: list[Candidate] = []
    seen_envs = set(named_envs)
    for candidate in _environments(document):
        if candidate.name not in seen_envs:
            seen_envs.add(candidate.name)
            found += [
                c
                for c in _each_declaration(document, order, ENVIRONMENT, candidate)
                if not covered(ENVIRONMENT, c)
            ]
    seen_commands = set(named_commands) | _known_commands(document)
    for candidate in _commands(document):
        if candidate.name not in seen_commands:
            seen_commands.add(candidate.name)
            found += [
                c
                for c in _each_declaration(document, order, COMMAND, candidate)
                if not covered(COMMAND, c)
            ]
    return tuple(found)


def declarations(document: Document, namespace: str, name: str) -> tuple[Span, ...]:
    """Every declaration of `name` in reading order: an environment's
    `\\newenvironment` and `\\renewenvironment`, and any other command
    whose braced argument it is (`\\newmdenv[…]{summarybox}`); a command's every
    definition (`\\newcommand`, `\\renewcommand`, `\\def` and the like)."""
    if namespace == COMMAND:
        found = [m.at for m in _macros(document) if m.name.lstrip("\\") == name]
    else:
        found = [
            m.at
            for m in _macros(document)
            if m.command in _ENVIRONMENT_COMMANDS and m.name == name
        ]
        found += [
            span
            for span in _declarations(name, _declaring_texts(document))
            if _DECLARING.fullmatch(_COMMAND_NAME.match(document.text(span)).group(1))
            and not any(s.file == span.file and s.start == span.start for s in found)
        ]
    return tuple(ReadingOrder.of(document).sort(found, key=lambda span: span))


def in_force(order: ReadingOrder, declared: Sequence[Span], use: Span) -> int:
    """Which of `declared` (in reading order) is in force at `use`: the
    last one before it, the first where none is."""
    at = order.key(use)
    index = 0
    for i, span in enumerate(declared):
        if order.key(span) <= at:
            index = i
    return index


def _each_declaration(
    document: Document, order: ReadingOrder, namespace: str, candidate: Candidate
) -> list[Candidate]:
    """`candidate` as listed: once where its name has one declaring text
    or none, else once for each text, its uses those where a declaration
    with that text is in force and its shape that declaration's own."""
    if candidate.shape not in (CandidateShape.ENVIRONMENT, CandidateShape.MACRO):
        return [candidate]
    declared = declarations(document, namespace, candidate.name)
    texts = list(dict.fromkeys(document.text(span) for span in declared))
    if len(texts) < 2:
        return [candidate]
    uses: dict[str, list[Span]] = {text: [] for text in texts}
    for use in _all_uses(document, namespace, candidate.name):
        uses[document.text(declared[in_force(order, declared, use)])].append(use)
    bodies = {
        document.text(m.at): m.definition
        for m in _macros(document)
        if m.name.lstrip("\\") == candidate.name
    }
    found = []
    for index, text in enumerate(texts):
        first = next(span for span in declared if document.text(span) == text)
        shape = candidate.shape
        if namespace == COMMAND and not references(bodies.get(text, "")):
            shape = CandidateShape.REDEFINED
        found.append(
            Candidate(
                candidate.name,
                shape,
                tuple(uses[text][:USES_SHOWN]),
                first,
                index,
                len(texts),
            )
        )
    return found


def _all_uses(document: Document, namespace: str, name: str) -> list[Span]:
    """Every use of `name`: an environment's openings outside a display, a
    float or a listing; a command's every occurrence in the body."""
    if namespace == ENVIRONMENT:
        units = [env.span for env in document.environments if env.kind in _UNITS]
        return [
            Span(
                env.span.file,
                env.span.start,
                env.span.start + len(f"\\begin{{{name}}}"),
            )
            for env in document.environments
            if env.name == name and not _inside(env.span, units)
        ]
    pattern = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z@])")
    return [
        Span(path, match.start(), match.end())
        for path, text in _body_texts(document)
        for match in pattern.finditer(text)
    ]


def _environments(document: Document) -> Iterator[Candidate]:
    texts = _declaring_texts(document)
    units = [env.span for env in document.environments if env.kind in _UNITS]
    uses: dict[str, list[Span]] = {}
    for env in document.environments:
        if env.kind is EnvKind.OTHER and not _inside(env.span, units):
            opening = len(f"\\begin{{{env.name}}}")
            uses.setdefault(env.name, []).append(
                Span(env.span.file, env.span.start, env.span.start + opening)
            )
    defined = {
        macro.name: macro.at
        for macro in reversed(_macros(document))
        if macro.command == "newenvironment"
    }
    for name, spans in uses.items():
        yield Candidate(
            name,
            CandidateShape.ENVIRONMENT,
            tuple(spans[:USES_SHOWN]),
            defined.get(name) or _declaration(name, texts),
        )
    known = {env.name for env in document.environments if env.kind is not EnvKind.OTHER}
    known |= set(STATEMENT_ENVS) | {found.name for found in document.declarations}
    for macro in _macros(document):
        if (
            macro.command == "newenvironment"
            and macro.name not in known
            and static_kind(macro.name) is None
        ):
            yield Candidate(macro.name, CandidateShape.ENVIRONMENT, (), macro.at)


def _commands(document: Document) -> Iterator[Candidate]:
    bodies = _body_texts(document)
    for macro in _macros(document):
        name = macro.name.lstrip("\\")
        if macro.command in _ENVIRONMENT_COMMANDS or not name:
            continue
        if references(macro.definition):
            yield Candidate(
                name,
                CandidateShape.MACRO,
                _uses_of(name, bodies),
                macro.at,
            )
    labels = {label.name for label in document.labels}
    prefixes = {label.split(":", 1)[0] + ":" for label in labels if ":" in label}
    uses: dict[str, list[Span]] = {}
    other_uses: set[str] = set()
    for path, text in bodies:
        for match in _COMMAND_RE.finditer(text):
            command = match.group(1)
            if command in _NOT_REFERENCES or "cite" in command:
                continue
            if _all_labels(match.group(2), labels, prefixes):
                uses.setdefault(command, []).append(
                    Span(path, match.start(), match.end())
                )
            else:
                other_uses.add(command)
    for command, spans in uses.items():
        if command not in other_uses:
            yield Candidate(
                command, CandidateShape.USE, tuple(spans[:USES_SHOWN]), None
            )
    dangling = [
        target
        for reference in document.references
        for target in reference.targets
        if target not in labels
    ]
    for target in dict.fromkeys(dangling):
        definer = _around(target, bodies)
        if definer is not None:
            yield Candidate(
                definer[0], CandidateShape.LABEL_DEFINER, (definer[1],), None
            )


def _known_commands(document: Document) -> set[str]:
    """The commands the reader already reads as references or labels: the
    standard ones, the paper's own and its packages' code reads, and every
    one it read a reference from."""
    return (
        set(reference_table(document.files).commands)
        | set(RANGE_COMMANDS)
        | {reference.command for reference in document.references}
        | _NOT_REFERENCES
    )


def _all_labels(argument: str, labels: set[str], prefixes: set[str]) -> bool:
    """Whether every item of a comma list is a label, or one less a prefix."""
    items = [item.strip() for item in argument.split(",")]
    return bool(items) and all(
        item and (item in labels or any(prefix + item in labels for prefix in prefixes))
        for item in items
    )


def _around(target: str, bodies: Iterable[tuple[str, str]]) -> tuple[str, Span] | None:
    """The first command, other than a reference, whose argument is `target`."""
    known = set(STANDARD_REFERENCES.commands) | set(RANGE_COMMANDS)
    for path, text in bodies:
        for match in _COMMAND_RE.finditer(text):
            command = match.group(1)
            if match.group(2).strip() == target and command not in known | {
                "begin",
                "end",
            }:
                return (command, Span(path, match.start(), match.end()))
    return None


def _uses_of(name: str, bodies: Iterable[tuple[str, str]]) -> tuple[Span, ...]:
    pattern = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z@])")
    spans = [
        Span(path, match.start(), match.end())
        for path, text in bodies
        for match in pattern.finditer(text)
    ]
    return tuple(spans[:USES_SHOWN])


def _declaration(name: str, texts: list[tuple[str, str]]) -> Span | None:
    """The first command in a declaring text whose braced argument is `name`,
    with every argument after it: `\\newmdenv[…]{summarybox}`,
    `\\newlist{steps}{enumerate}{2}`, `\\newtcbtheorem{rule}{Rule}{…}{rl}`.
    What follows the name says what the name is."""
    return next(iter(_declarations(name, texts)), None)


def _declarations(name: str, texts: list[tuple[str, str]]) -> list[Span]:
    """Every command in the declaring texts whose braced argument is
    `name`, as `_declaration` reads the first."""
    pattern = re.compile(
        r"\\([A-Za-z@]+)\*?(?:\[[^\]]*\])*\{" + re.escape(name) + r"\}"
    )
    found = []
    for path, text in texts:
        for match in pattern.finditer(text):
            if match.group(1) not in ("begin", "end"):
                groups = list(argument_groups(text, match.end()))
                end = groups[-1][2] if groups else match.end()
                found.append(Span(path, match.start(), end))
    return found


def _macros(document: Document) -> list[Macro]:
    """The paper's definitions, a Markdown file's `header-includes` among
    them."""
    found = list(document.macros)
    for source in document.files:
        if source.format is not Format.LATEX:
            found.extend(macros_in(source.path, declaring_text(source)))
    return found


def _declaring_texts(document: Document) -> list[tuple[str, str]]:
    return [(source.path, declaring_text(source)) for source in document.files]


def _body_texts(document: Document) -> list[tuple[str, str]]:
    """Each rendered file's text, every hidden run blanked, the length kept."""
    return [
        (source.path, _visible(source))
        for source in document.files
        if source.role in (FileRole.ROOT, FileRole.BODY)
    ]


def _visible(source: SourceFile) -> str:
    kept = list(source.text)
    for hidden in source.hidden:
        for index in range(hidden.span.start, hidden.span.end):
            if kept[index] != "\n":
                kept[index] = " "
    return "".join(kept)


def _inside(span: Span, units: list[Span]) -> bool:
    return any(
        unit.file == span.file and unit.start <= span.start and span.end <= unit.end
        for unit in units
        if unit != span
    )


__all__ = [
    "USES_SHOWN",
    "candidates",
]
