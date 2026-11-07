"""Format registry.

One entry per syntax, chosen per file by its extension: a `.tex` file is
plain LaTeX and anything else Markdown+LaTeX. Per file and not per paper,
because a paper may hold both.

A `.sty` or `.cls` shipped beside a paper is LaTeX too, since such files
are read through the same passes.
"""

from __future__ import annotations

from pathlib import Path

from axiomation.reader.formats.latex import LatexFormat
from axiomation.reader.formats.md_latex import (
    STANDARD_ENVIRONMENTS,
    EnvironmentTable,
    MdLatexFormat,
)

DEFAULT_FORMAT = "md-latex"
LATEX_FORMAT = "latex"

_FORMATS: dict[str, type[MdLatexFormat]] = {
    DEFAULT_FORMAT: MdLatexFormat,
    LATEX_FORMAT: LatexFormat,
}

#: The extensions that select a format other than the default.
_BY_SUFFIX: dict[str, str] = {
    ".tex": LATEX_FORMAT,
    ".sty": LATEX_FORMAT,
    ".cls": LATEX_FORMAT,
}


class UnknownFormatError(ValueError):
    """A format name that is not registered."""


def format_for(
    path: Path | str, environments: EnvironmentTable = STANDARD_ENVIRONMENTS
) -> MdLatexFormat:
    """The format a file is read in, from its extension, reading the paper's
    environment table."""
    return get_format(
        _BY_SUFFIX.get(Path(path).suffix.lower(), DEFAULT_FORMAT), environments
    )


def is_latex(path: Path | str) -> bool:
    """Whether a file is read as plain LaTeX."""
    return format_for(path).name == LATEX_FORMAT


def get_format(
    name: str = DEFAULT_FORMAT,
    environments: EnvironmentTable = STANDARD_ENVIRONMENTS,
) -> MdLatexFormat:
    """Return the handler for a format name.

    A handler is cheap to build, so none is cached. The table decides the
    statement environments it reads.
    """
    handler = _FORMATS.get(name)
    if handler is None:
        available = ", ".join(sorted(_FORMATS))
        raise UnknownFormatError(
            f"unknown document format {name!r}. Available: {available}"
        )
    return handler(environments)


__all__ = [
    "DEFAULT_FORMAT",
    "EnvironmentTable",
    "LATEX_FORMAT",
    "LatexFormat",
    "MdLatexFormat",
    "STANDARD_ENVIRONMENTS",
    "UnknownFormatError",
    "format_for",
    "get_format",
    "is_latex",
]
