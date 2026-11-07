"""The files a paper is made of: its root, and what the root pulls in.

The document is what compiles, not every file in the
directory. The walk starts at the root and follows each
include where it stands, depth first, which is the order LaTeX reads:

- an include in a preamble reaches a `PREAMBLE` file, read for its
  declarations and macros only;
- one in the body reaches a `BODY` file, read for everything;
- `\\usepackage`, `\\RequirePackage` and `\\documentclass` in a preamble
  reach a `.sty` or `.cls` shipped beside the paper, a `PACKAGE` file. A
  package that is not shipped is not an include;
- a Markdown file's YAML `header-includes` is the preamble pandoc writes:
  an include or a package there is followed as one in a preamble;
- an include in a comment, in `\\iffalse`, in a verbatim block or after the
  root's `\\end{document}` is not followed, since LaTeX never reads it. A
  path built from a macro is recorded unresolved;
- a file is read once, so a cycle stops.

Every `.tex` and `.md` file under the directory that the walk does not reach
is listed as never included.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from axiomation.reader.declarations import declaring_text
from axiomation.reader.document import FileRole, Format, Include, SourceFile, Span
from axiomation.reader.formats import is_latex
from axiomation.reader.formats.latex import (
    BEGIN_DOCUMENT_RE,
    END_DOCUMENT_RE,
    read_mask,
)
from axiomation.reader.formats.md_latex import include_targets, resolved_include

#: A package or a class the paper may ship beside itself.
_PACKAGE_RE = re.compile(
    r"\\(usepackage|RequirePackage|documentclass)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}"
)
_SUFFIX = {"usepackage": ".sty", "RequirePackage": ".sty", "documentclass": ".cls"}
#: The files a paper's text may be written in.
TEXT_SUFFIXES = (".tex", ".md")
#: The root of a Markdown paper whose directory holds several `.md` files.
MARKDOWN_ROOT = "paper.md"
#: The directory beside a paper's files that holds its store, the default
#: `--store`: never a paper's file, whatever it holds.
STORE_DIRECTORY = "axiomation"


class ReaderError(Exception):
    """A paper the reader cannot read as given."""


def find_root(directory: Path, root: Path | None = None) -> Path:
    """The paper's root file.

    The one `.tex` file in the directory with an uncommented
    `\\begin{document}`, otherwise the one `.md` file, otherwise `paper.md`.
    `root`, relative to the directory or absolute, overrides.
    """
    directory = directory.resolve()
    if root is not None:
        given = (root if root.is_absolute() else directory / root).resolve()
        if not given.is_file() or not given.is_relative_to(directory):
            raise ReaderError(f"the root {root} is not a file in {directory}")
        return given
    documents = [
        path
        for path in sorted(directory.glob("*.tex"))
        if path.is_file() and BEGIN_DOCUMENT_RE.search(read_mask(_read(path)).visible)
    ]
    if len(documents) == 1:
        return documents[0]
    if len(documents) > 1:
        raise ReaderError(
            f"{directory} has several roots, each with a \\begin{{document}}: "
            f"{_names(documents)}. Name one with --root."
        )
    markdown = [path for path in sorted(directory.glob("*.md")) if path.is_file()]
    if len(markdown) == 1:
        return markdown[0]
    if (directory / MARKDOWN_ROOT).is_file():
        return directory / MARKDOWN_ROOT
    candidates = _names(markdown) if markdown else "none"
    raise ReaderError(
        f"{directory} has no root: no .tex file with a \\begin{{document}}, and "
        f"no single .md file or {MARKDOWN_ROOT} (.md files: {candidates}). "
        "Name one with --root."
    )


@dataclass(frozen=True, slots=True)
class Walk:
    """What the walk found: the files in the order it read them, the
    includes in reading order, and the text files it never reached."""

    files: tuple[SourceFile, ...]
    includes: tuple[Include, ...]
    never_included: tuple[str, ...]


def walk(directory: Path, root: Path) -> Walk:
    """Read the root and every file it reaches, depth first.

    Each `SourceFile` comes with no hidden spans; the layer fills them.
    """
    directory = directory.resolve()
    root = root.resolve()
    files: list[SourceFile] = []
    includes: list[Include] = []
    seen: set[Path] = set()

    def visit(path: Path, role: FileRole) -> None:
        seen.add(path)
        data = path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        source = SourceFile(
            path=_relative(path, directory),
            role=role,
            format=Format.LATEX if is_latex(path) else Format.MARKDOWN,
            sha256=hashlib.sha256(data).hexdigest(),
            text=text,
            hidden=(),
        )
        files.append(source)
        for include, target in _includes(path, source, directory):
            includes.append(include)
            if target is not None and target not in seen:
                visit(target, include.role)

    visit(root, FileRole.ROOT)
    never = sorted(
        _relative(path, directory)
        for path in directory.rglob("*")
        if path.suffix.lower() in TEXT_SUFFIXES
        and path.is_file()
        and not _hidden(path, directory)
        and path.relative_to(directory).parts[0] != STORE_DIRECTORY
        and path.resolve() not in seen
    )
    return Walk(tuple(files), tuple(includes), tuple(never))


def _includes(
    path: Path, source: SourceFile, directory: Path
) -> list[tuple[Include, Path | None]]:
    """One file's includes, in the order they stand, each with the file it
    reaches, if any: a LaTeX file's, and a Markdown file's `header-includes`'."""
    visible = declaring_text(source)
    preamble_end = 0
    body_end = len(visible)
    if source.format is not Format.LATEX or source.role in (
        FileRole.PREAMBLE,
        FileRole.PACKAGE,
    ):
        preamble_end = len(visible)
    elif source.role is FileRole.ROOT:
        begin = BEGIN_DOCUMENT_RE.search(visible)
        preamble_end = begin.start() if begin else 0
        end = END_DOCUMENT_RE.search(visible, begin.end() if begin else 0)
        body_end = end.start() if end else len(visible)
    found: list[tuple[Include, Path | None]] = []
    for include in include_targets(visible[:body_end]):
        role = FileRole.PREAMBLE if include.start < preamble_end else FileRole.BODY
        target = None
        if "\\" not in include.target and "#" not in include.target:
            target = _existing(resolved_include(path, include.target, directory))
            target = target if target and _inside(target, directory) else None
        found.append(
            (
                Include(
                    command=include.command,
                    target=include.target,
                    at=Span(source.path, include.start, include.end),
                    resolved=_relative(target, directory) if target else None,
                    role=role,
                ),
                target,
            )
        )
    for match in _PACKAGE_RE.finditer(visible, 0, preamble_end):
        suffix = _SUFFIX[match.group(1)]
        for package in match.group(2).split(","):
            name = package.strip()
            if not name:
                continue
            target = _existing(resolved_include(path, name, directory, suffix))
            if target is None or not _inside(target, directory):
                continue
            found.append(
                (
                    Include(
                        command=match.group(1),
                        target=name,
                        at=Span(source.path, match.start(), match.end()),
                        resolved=_relative(target, directory),
                        role=FileRole.PACKAGE,
                    ),
                    target,
                )
            )
    found.sort(key=lambda item: item[0].at.start)
    return found


def _existing(candidates: tuple[Path, ...]) -> Path | None:
    """The first candidate on disk, as LaTeX takes the first file it finds."""
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _inside(path: Path, directory: Path) -> bool:
    return path.resolve().is_relative_to(directory)


def _relative(path: Path, directory: Path) -> str:
    return path.resolve().relative_to(directory).as_posix()


def _hidden(path: Path, directory: Path) -> bool:
    """Whether a path lies in a hidden directory, or is a hidden file."""
    return any(part.startswith(".") for part in path.relative_to(directory).parts)


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _names(paths: list[Path]) -> str:
    return ", ".join(path.name for path in paths)
