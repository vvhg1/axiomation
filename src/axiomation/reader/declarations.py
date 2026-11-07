"""The environments and the reference commands a paper declares.

Most LaTeX papers name
their own theorem environments in the preamble — `\\newtheorem{thm}{Theorem}`
— and a `\\begin{thm}` the reader does not know bounds nothing. The
declaration is text the author wrote, so reading it is as deterministic as
reading a `\\label`.

What is read, and where:

- **The files.** Every LaTeX file the include walk reached (`includes.walk`),
  whatever its role: the root, the body files, and the preamble files and
  shipped packages that are read for their declarations only. The walk
  follows the preambles once for every pass. A comment, an `\\iffalse`
  block and a verbatim block declare nothing. A Markdown file declares only
  in its YAML header's `header-includes`, the LaTeX pandoc puts in the
  preamble.
- **The spellings.** A statement: `\\newtheorem` and `\\newtheorem*`
  (amsthm, ntheorem, plain LaTeX), `\\spnewtheorem` (Springer),
  `\\declaretheorem` (thmtools), `\\newmdtheoremenv` (mdframed),
  `\\newtcbtheorem` (tcolorbox). A frame, read as a container:
  `\\newmdenv` (mdframed), `\\newtcolorbox` (tcolorbox). A wrapper: a
  `\\newenvironment` whose begin code opens one environment and whose end
  code closes it, read as that environment when the reader knows it by
  name or as a statement (`\\newenvironment{proofsketch}{\\begin{proof}[Sketch]}
  {\\end{proof}}` is a proof); any other `\\newenvironment` is the
  vocabulary map's. The first declaration of a name wins, as LaTeX
  refuses the second.
- **The meaning.** The last word of the printed name, when it is a standard
  environment: `Main Theorem` means `theorem`. A printed name that is
  `\\<word>name`, babel's and LyX's convention (`\\protect\\theoremname`),
  prints `<word>`, and means it when that is a standard environment.
  Otherwise a standard name keeps its own meaning, and anything else
  means nothing; it still bounds a statement.

The reference commands, read the same way, from the same files:

- **A paper's own.** A one-argument `\\newcommand`, `\\renewcommand`,
  `\\providecommand`, `\\DeclareRobustCommand` or `\\def` whose body is one
  standard reference over `#1`, a label prefix before it and one word
  before that, in parentheses or not: `\\newcommand{\\lemref}[1]{Lemma~\\ref{lem:#1}}`
  points at `lem:<its argument>` and prints `Lemma`. The first definition
  of a name wins and a redefinition replaces it, as in LaTeX; a body any
  richer is the vocabulary map's.
- **A package's.** Where the paper loads it (a class loading it counts):
  jmlrutils' `\\lemmaref` and its siblings, over a comma list, each
  printing its word; refstyle's `\\figref`, `\\Figref` and the other
  default types, over a comma list, each adding its `type:` prefix
  unless the package is loaded `nokeyprefix`; fancyref's `\\fref` and
  `\\Fref`; prettyref's `\\prettyref`. A package's command replaces the
  standard one it redefines (keyed refstyle's `\\eqref` adds `eq:`); the
  paper's `\\renewcommand` or `\\def` replaces a package's, and a
  package's wins over the paper's `\\providecommand`.

Nothing here expands a macro. A name built from one is read as written.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from typing import Any

from axiomation.reader.document import (
    Declaration,
    EnvKind,
    Format,
    RefEntry,
    SourceFile,
    Span,
)
from axiomation.reader.environments import static_kind
from axiomation.reader.formats.latex import read_mask
from axiomation.reader.formats.md_latex import (
    LIST_COMMANDS,
    REFERENCE_COMMANDS,
    RESERVED_ENVS,
    STANDARD_ENVIRONMENTS,
    STANDARD_REFERENCES,
    STATEMENT_ENVS,
    EnvironmentTable,
    ReferenceTable,
    header_includes,
)

#: The commands that declare a theorem-like environment. The lookahead
#: keeps `\\newtheoremstyle` and `\\declaretheoremstyle` out.
_DECLARE_RE = re.compile(
    r"\\(newtheorem|spnewtheorem|declaretheorem|newmdtheoremenv|newtcbtheorem)"
    r"(\*?)(?![a-zA-Z@])"
)
#: The declaring commands whose options come before the name.
_LEADING_OPTIONS = frozenset({"newmdtheoremenv", "newtcbtheorem"})
#: The commands that declare a frame: a box around whatever the paper puts
#: in it.
_FRAME_RE = re.compile(r"\\(newmdenv|newtcolorbox)(?![a-zA-Z@])")
_WRAPPER_RE = re.compile(r"\\newenvironment(?![a-zA-Z@])\*?")
_BEGIN_RE = re.compile(r"\\begin\s*\{([^{}]+)\}")
_END_RE = re.compile(r"\\end\s*\{([^{}]+)\}")
#: A one-argument definition: its name, the body's braces next.
_OWN_COMMAND_RE = re.compile(
    r"\\(?P<how>newcommand|renewcommand|providecommand|DeclareRobustCommand)"
    r"\*?\s*(?:\{\s*\\(?P<braced>[A-Za-z@]+)\s*\}|\\(?P<bare>[A-Za-z@]+))"
    r"\s*\[\s*1\s*\]\s*(?=\{)"
    r"|\\[gex]?def\s*\\(?P<defined>[A-Za-z@]+)\s*#1\s*(?=\{)"
)
#: The definitions that replace one already made, a package's included.
_REDEFINING = frozenset({"renewcommand", "DeclareRobustCommand", "def"})
#: A body that is one standard reference over `#1`: the word it prints,
#: the command, the label prefix.
_OWN_BODY_RE = re.compile(
    r"\s*(?:(?P<word>[A-Za-z]+)\.?[\s~]*)?(?P<open>\()?\\(?P<command>"
    + "|".join(REFERENCE_COMMANDS)
    + r")\*?\{(?P<prefix>[^{}#\s]*)#1\}(?(open)\))\s*"
)
_PACKAGE_RE = re.compile(
    r"\\(?P<command>usepackage|RequirePackage|documentclass)\s*"
    r"(?:\[(?P<options>[^\]]*)\])?\s*\{(?P<names>[^}]*)\}"
)
#: The packages a class loads, where one of them names references.
_CLASS_PACKAGES: dict[str, tuple[str, ...]] = {
    "jmlr": ("jmlrutils",),
    "jmlrbook": ("jmlrutils",),
}
#: jmlrutils' reference commands, `\\<name>ref`, and the word each prints.
_JMLR_WORDS: tuple[tuple[str, str], ...] = (
    ("section", "Section"),
    ("equation", "Equation"),
    ("table", "Table"),
    ("figure", "Figure"),
    ("algorithm", "Algorithm"),
    ("theorem", "Theorem"),
    ("lemma", "Lemma"),
    ("remark", "Remark"),
    ("corollary", "Corollary"),
    ("definition", "Definition"),
    ("conjecture", "Conjecture"),
    ("axiom", "Axiom"),
    ("example", "Example"),
    ("appendix", "Appendix"),
    ("part", "Part"),
)
#: refstyle's default types (its `refstyle.cfg`), `\\<type>ref` printing
#: the word and `\\<Type>ref` the word capitalized.
_REFSTYLE_TYPES: tuple[tuple[str, str], ...] = (
    ("part", "part"),
    ("chap", "chapter"),
    ("sec", "section"),
    ("eq", "equation"),
    ("fig", "figure"),
    ("tab", "table"),
    ("fn", "footnote"),
)
#: The thmtools keys that set the printed name.
_NAME_KEYS = ("name", "title", "heading")
_COMMAND_RE = re.compile(r"\\[a-zA-Z@]+\*?")
_WORD_RE = re.compile(r"[A-Za-z]+")
#: `\\theoremname`, `\\lemmaname`: the word a language package prints.
_NAME_MACRO_RE = re.compile(r"\\([a-z]+)name(?![a-zA-Z@])")


def meaning(name: str, printed: str) -> str | None:
    """The standard environment a declared name is written for, or `None`."""
    words = _WORD_RE.findall(_COMMAND_RE.sub(" ", _printed_words(printed)))
    head = words[-1].lower() if words else ""
    if head in STATEMENT_ENVS:
        return head
    return name if name in STATEMENT_ENVS else None


def read_declarations(
    files: Sequence[SourceFile], order: Callable[[Span], Any] | None = None
) -> tuple[Declaration, ...]:
    """What the paper's LaTeX files declare, the first declaration of a name
    winning, as LaTeX refuses the second.

    "First" is reading order, `order` a key over spans that gives it (the
    layer's `ReadingOrder.key`): a declaration in a file a preamble
    `\\input`s comes before the root's declarations below that `\\input`.
    Without `order`, the files come in the order given and each
    in its own order. A wrapper is read once every other declaration is,
    so it may wrap a statement declared after it.
    """
    candidates: list[Declaration] = []
    wrappers: list[tuple[str, str, Span]] = []
    for source in files:
        visible = declaring_text(source)
        for match in _DECLARE_RE.finditer(visible):
            end, pairs = _declared(match, visible)
            at = Span(source.path, match.start(), end)
            if match.group(1) == "newtcbtheorem":
                candidates.extend(
                    declared
                    for name, printed in pairs
                    for declared in _boxed(name, printed, at, visible, match)
                )
                continue
            candidates.extend(
                Declaration(name, printed, meaning(name, printed), at)
                for name, printed in pairs
            )
        for match in _FRAME_RE.finditer(visible):
            found = _framed(match, visible)
            if found is not None:
                name, end = found
                candidates.append(
                    Declaration(
                        name,
                        "",
                        None,
                        Span(source.path, match.start(), end),
                        EnvKind.CONTAINER,
                    )
                )
        for match in _WRAPPER_RE.finditer(visible):
            found = _wrapping(match, visible)
            if found is not None:
                name, inner, end = found
                wrappers.append((name, inner, Span(source.path, match.start(), end)))
    if order is not None:
        candidates.sort(key=lambda found: order(found.at))
        wrappers.sort(key=lambda found: order(found[2]))
    first: dict[str, Declaration] = {}
    for found in candidates:
        first.setdefault(found.name, found)
    for found in _wrapped(wrappers, first):
        first.setdefault(found.name, found)
    declared = list(first.values())
    if order is not None:
        declared.sort(key=lambda found: order(found.at))
    return tuple(declared)


def _boxed(
    name: str, printed: str, at: Span, text: str, match: re.Match[str]
) -> tuple[Declaration, Declaration]:
    """A tcolorbox theorem: the numbered name, which takes a label with the
    declaration's prefix, and the starred, which takes none."""
    groups = argument_groups(text, match.end())
    braced = [body for kind, body, _ in groups if kind == "{"]
    prefix = braced[3].strip() if len(braced) > 3 else ""
    means = meaning(name, printed)
    return (
        Declaration(name, printed, means, at, label_prefix=prefix),
        Declaration(f"{name}*", printed, means, at),
    )


def _wrapped(
    wrappers: Sequence[tuple[str, str, Span]], declared: dict[str, Declaration]
) -> list[Declaration]:
    """Each wrapper read as the environment it wraps: a statement as that
    statement, a frame as a frame, a name the reader knows by name as that
    one; a wrapper of a wrapper as what that one is read as. A wrapper of
    anything else is no declaration: the vocabulary map answers it."""
    known = dict(declared)
    found: list[Declaration] = []
    pending = [w for w in wrappers if w[0] not in known and _wrappable(w[0])]
    while pending:
        unread = []
        for name, inner, at in pending:
            reading = _reading(inner, known)
            if reading is None:
                unread.append((name, inner, at))
                continue
            printed, means, kind = reading
            known.setdefault(name, Declaration(name, printed, means, at, kind))
            found.append(known[name])
        if len(unread) == len(pending):
            break
        pending = unread
    return found


def _wrappable(name: str) -> bool:
    """A name a wrapper may declare: not one the reader reads by its own
    name, whatever a paper declares."""
    return name not in RESERVED_ENVS and static_kind(name) is None


def _reading(
    inner: str, known: dict[str, Declaration]
) -> tuple[str, str | None, EnvKind] | None:
    """What a wrapper of `inner` is read as: `(printed, meaning, kind)`."""
    if inner in known:
        found = known[inner]
        return (found.printed, found.meaning, found.kind)
    if inner in STATEMENT_ENVS:
        return (inner[:1].upper() + inner[1:], inner, EnvKind.STATEMENT)
    kind = static_kind(inner)
    if kind is not None:
        return ("", inner.rstrip("*"), kind)
    return None


def declaring_text(source: SourceFile) -> str:
    """The part of a file that can declare, every other character blanked
    and the length kept, so offsets stay the file's: a LaTeX file less its
    comments and disabled blocks, a Markdown file's `header-includes`."""
    if source.format is Format.LATEX:
        return read_mask(source.text).visible
    kept = [" " if char != "\n" else char for char in source.text]
    for start, end in header_includes(source.text):
        kept[start:end] = source.text[start:end]
    return read_mask("".join(kept)).visible


def environment_table(declarations: Sequence[Declaration]) -> EnvironmentTable:
    """The standard environments with the declared ones added: the
    statements, the frames, and the wrappers of a name the reader knows."""
    statements = [d for d in declarations if d.kind is EnvKind.STATEMENT]
    frames = [
        d.name for d in declarations if d.kind is EnvKind.CONTAINER and not d.meaning
    ]
    wrappers = [
        (d.name, d.meaning)
        for d in declarations
        if d.kind is not EnvKind.STATEMENT and d.meaning
    ]
    boxed = tuple(
        (d.name, d.label_prefix) for d in statements if d.label_prefix is not None
    )
    table = STANDARD_ENVIRONMENTS.declaring(
        (found.name, found.meaning) for found in statements
    ).mapping((), frames, (), wrappers)
    return replace(table, boxed=boxed)


def reference_table(files: Sequence[SourceFile]) -> ReferenceTable:
    """The standard reference commands with the paper's own and its loaded
    packages' added, read as LaTeX does: a package's command over the
    standard one it redefines, the paper's `\\renewcommand` or `\\def`
    over a package's, a package's over the paper's `\\providecommand`."""
    packages = package_references(files)
    own, redefined = _own_references(files, {e.command for e in packages})
    named = {entry.command for entry in own} | redefined
    entries = [
        (entry.command, entry.takes_list, entry.prefix, entry.word)
        for entry in (*own, *(e for e in packages if e.command not in named))
    ]
    return STANDARD_REFERENCES.overriding(entries).mapping(entries)


def own_references(files: Sequence[SourceFile]) -> tuple[RefEntry, ...]:
    """The paper's own one-argument reference commands, the first
    definition of a name winning, a redefinition replacing it."""
    return _own_references(files, set())[0]


def _own_references(
    files: Sequence[SourceFile], packaged: set[str]
) -> tuple[tuple[RefEntry, ...], set[str]]:
    """The paper's own reference commands, and every name it redefines
    with a one-argument body that is none. A `\\providecommand` of a name
    a loaded package defines does nothing, as in LaTeX."""
    found: dict[str, RefEntry] = {}
    defined: set[str] = set()
    redefined: set[str] = set()
    for source in files:
        visible = declaring_text(source)
        for match in _OWN_COMMAND_RE.finditer(visible):
            name = (
                match.group("braced") or match.group("bare") or match.group("defined")
            )
            how = match.group("how") or "def"
            groups = list(argument_groups(visible, match.end()))
            if not groups or groups[0][0] != "{":
                continue
            replaces = how in _REDEFINING
            if not replaces and (
                name in defined or (how == "providecommand" and name in packaged)
            ):
                continue
            defined.add(name)
            body = _OWN_BODY_RE.fullmatch(groups[0][1])
            if body is None:
                found.pop(name, None)
                if replaces:
                    redefined.add(name)
                continue
            prefix = body.group("prefix")
            found[name] = RefEntry(
                name,
                body.group("command") in LIST_COMMANDS and not prefix,
                prefix,
                body.group("word"),
            )
    return tuple(found.values()), redefined - set(found)


def package_references(files: Sequence[SourceFile]) -> tuple[RefEntry, ...]:
    """The reference commands of the packages the paper loads."""
    loaded = loaded_packages(files)
    found: list[RefEntry] = []
    if "jmlrutils" in loaded:
        found.extend(
            RefEntry(f"{name}ref", True, "", word) for name, word in _JMLR_WORDS
        )
    if "refstyle" in loaded:
        keyed = "nokeyprefix" not in loaded["refstyle"]
        for kind, word in _REFSTYLE_TYPES:
            prefix = f"{kind}:" if keyed else ""
            found.append(RefEntry(f"{kind}ref", True, prefix, word))
            found.append(
                RefEntry(f"{kind.capitalize()}ref", True, prefix, word.capitalize())
            )
    if "fancyref" in loaded:
        found.extend((RefEntry("fref"), RefEntry("Fref")))
    if "prettyref" in loaded:
        found.append(RefEntry("prettyref"))
    return tuple(found)


def loaded_packages(files: Sequence[SourceFile]) -> dict[str, frozenset[str]]:
    """Each package the paper loads, a class's own among them, with the
    options it is loaded with."""
    loaded: dict[str, set[str]] = {}
    for source in files:
        for match in _PACKAGE_RE.finditer(declaring_text(source)):
            options = {
                option.strip()
                for option in (match.group("options") or "").split(",")
                if option.strip()
            }
            for name in (n.strip() for n in match.group("names").split(",")):
                if not name:
                    continue
                packages = (
                    _CLASS_PACKAGES.get(name, ())
                    if match.group("command") == "documentclass"
                    else (name,)
                )
                for package in packages:
                    loaded.setdefault(package, set()).update(options)
    return {name: frozenset(options) for name, options in loaded.items()}


def _declared(match: re.Match[str], text: str) -> tuple[int, list[tuple[str, str]]]:
    """Where one declaring command ends, and the `(name, printed)` pairs it
    makes."""
    command = match.group(1)
    groups = list(argument_groups(text, match.end()))
    end = groups[-1][2] if groups else match.end()
    if command == "declaretheorem":
        options = " ".join(body for kind, body, _ in groups[:3] if kind == "[")
        names = next((body for kind, body, _ in groups if kind == "{"), None)
        if names is None:
            return (end, [])
        printed = _option(options, _NAME_KEYS)
        return (
            end,
            [
                (name, printed if printed is not None else name[:1].upper() + name[1:])
                for name in (part.strip() for part in names.split(","))
                if name
            ],
        )
    braced = []
    for kind, body, group_end in groups:
        if kind == "{":
            braced.append(body)
        if len(braced) == 2:
            end = _declaration_end(groups, group_end)
            break
    if len(braced) < 2 or (groups[0][0] != "{" and command not in _LEADING_OPTIONS):
        return (end, [])
    if command == "newtcbtheorem":
        # its display options and label prefix follow the printed name
        end = groups[-1][2]
    name = braced[0].strip()
    return (end, [(name, _plain(braced[1]))] if name else [])


def _framed(match: re.Match[str], text: str) -> tuple[str, int] | None:
    """The name a frame command declares, and where the command ends."""
    groups = list(argument_groups(text, match.end()))
    name = next((body.strip() for kind, body, _ in groups if kind == "{"), "")
    return (name, groups[-1][2]) if name else None


def _wrapping(match: re.Match[str], text: str) -> tuple[str, str, int] | None:
    """`(name, inner, end)` for a `\\newenvironment` whose begin code opens
    one environment, `inner`, and whose end code closes it; `None` for any
    other."""
    groups = list(argument_groups(text, match.end()))
    braced = [body for kind, body, _ in groups if kind == "{"]
    if len(braced) < 3 or groups[0][0] != "{":
        return None
    name, begin, end = braced[0].strip(), braced[1], braced[2]
    opened = _BEGIN_RE.findall(begin)
    closed = _END_RE.findall(end)
    if (
        not name
        or len(opened) != 1
        or closed != opened
        or _END_RE.search(begin)
        or _BEGIN_RE.search(end)
    ):
        return None
    return (name, opened[0].strip(), groups[-1][2])


def _declaration_end(groups: list[tuple[str, str, int]], printed_end: int) -> int:
    """A `\\newtheorem` ends with its printed name, or with the `[within]`
    counter right after it."""
    after = [group for group in groups if group[2] > printed_end]
    if after and after[0][0] == "[":
        return after[0][2]
    return printed_end


def argument_groups(text: str, start: int) -> Iterator[tuple[str, str, int]]:
    """The `{...}` and `[...]` groups that follow a command, in order, each
    with the index just past it."""
    index = start
    while True:
        while index < len(text) and text[index] in " \t\n":
            index += 1
        if index >= len(text) or text[index] not in "{[":
            return
        opener = text[index]
        closer = "}" if opener == "{" else "]"
        depth = 0
        for end in range(index + 1, len(text)):
            char = text[end]
            if char == closer and depth == 0:
                break
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
        else:
            return
        yield (opener, text[index + 1 : end], end + 1)
        index = end + 1


def _option(options: str, keys: Sequence[str]) -> str | None:
    """The value of the first of `keys` in a `key=value` list, braces dropped."""
    depth = 0
    current = ""
    parts: list[str] = []
    for char in options:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    for part in parts:
        key, _, value = part.partition("=")
        if key.strip() in keys and value.strip():
            return _plain(value)
    return None


def _printed_words(printed: str) -> str:
    """`\\theoremname` as the word it prints, `Theorem`; every other command
    as written. `meaning` still takes the word only when it is a standard
    environment."""
    return _NAME_MACRO_RE.sub(lambda match: f" {match.group(1).capitalize()} ", printed)


def _plain(printed: str) -> str:
    """A printed name as it reads: braces and font commands dropped."""
    text = _COMMAND_RE.sub(" ", _printed_words(printed))
    text = text.replace("{", " ").replace("}", " ")
    return " ".join(text.split())


__all__ = [
    "argument_groups",
    "declaring_text",
    "environment_table",
    "loaded_packages",
    "meaning",
    "own_references",
    "package_references",
    "read_declarations",
    "reference_table",
]
