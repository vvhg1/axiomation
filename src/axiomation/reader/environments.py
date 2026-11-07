"""The environments the reader reads by their own name, the same in every
paper: proofs, lists, displays, floats, frames and listings. A paper's
declarations and its vocabulary map name the rest; neither changes these.
"""

from __future__ import annotations

from axiomation.reader.document import EnvKind
from axiomation.reader.formats.latex import COMMENT_ENV, VERBATIM_ENVS
from axiomation.reader.formats.md_latex import LIST_ENVS, EnvironmentTable

DISPLAY_ENVS: frozenset[str] = frozenset(
    {
        "equation",
        "align",
        "alignat",
        "gather",
        "multline",
        "flalign",
        "eqnarray",
        "displaymath",
    }
)
FLOAT_ENVS: frozenset[str] = frozenset({"figure", "table", "algorithm"})
#: The environments that only wrap their body: a frame (`mdframed`,
#: `framed`, tcolorbox's `tcolorbox`) holds whatever the paper puts in it,
#: a statement, a remark or a list, so its body is read as if it stood
#: alone.
CONTAINER_ENVS: frozenset[str] = frozenset(
    {
        "document",
        "abstract",
        "center",
        "flushleft",
        "flushright",
        "minipage",
        "mdframed",
        "framed",
        "tcolorbox",
    }
)
LISTING_ENVS: frozenset[str] = frozenset(
    env.rstrip("*") for env in VERBATIM_ENVS if env != COMMENT_ENV
)


def known_environments(table: EnvironmentTable) -> frozenset[str]:
    """Every environment the reader knows by name: the statements a table
    holds, and those it reads the same in every paper. A map's `wraps`
    names one of these."""
    return frozenset(table.names) | frozenset(
        {
            "proof",
            *LIST_ENVS,
            *DISPLAY_ENVS,
            *FLOAT_ENVS,
            *CONTAINER_ENVS,
            *LISTING_ENVS,
        }
    )


def static_kind(name: str) -> EnvKind | None:
    """The kind the reader gives `name` whatever a paper declares, or `None`
    when only a statement table or a vocabulary map can say."""
    base = name.rstrip("*")
    if base == "proof":
        return EnvKind.PROOF
    if name in LIST_ENVS:
        return EnvKind.LIST
    if base in DISPLAY_ENVS:
        return EnvKind.DISPLAY
    if base in FLOAT_ENVS:
        return EnvKind.FLOAT
    if name in CONTAINER_ENVS:
        return EnvKind.CONTAINER
    if base in LISTING_ENVS:
        return EnvKind.VERBATIM
    return None


__all__ = [
    "CONTAINER_ENVS",
    "DISPLAY_ENVS",
    "FLOAT_ENVS",
    "LISTING_ENVS",
    "known_environments",
    "static_kind",
]
