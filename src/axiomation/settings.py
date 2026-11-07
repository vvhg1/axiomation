"""A paper's settings, `axiomation.toml`: the paper's own
file and its workspace's, each section the paper's keys over the
workspace's.

A workspace is the nearest directory above a paper whose `axiomation.toml`
holds a `[workspace]` table whose `papers` pattern (default `papers/*`)
takes the paper in. `[computations]` is the core's first section; `[budget]`
holds what a run may spend without `--budget`. `[sources]` is read through
here by the cited works' settings (`sources/config.py`).

These files are shared with whoever has the paper, so a fact of one
person's machine or a secret has no place in them: a key that names one
is refused, naming the user file it goes in (`user_settings.py`).

Standard library only.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from axiomation.user_settings import UserKey

CONFIG_NAME = "axiomation.toml"
#: The `[workspace]` table's pattern for its papers when it names none.
DEFAULT_PAPERS = "papers/*"


class SettingsError(ValueError):
    """A settings file that cannot be read, or a section it cannot hold."""


#: The section of what a run may spend, and its one key: the dollars a
#: run that asks a model takes as its budget where no `--budget` is given.
BUDGET_SECTION = "budget"
RUN_DOLLARS_MAX = "run_dollars_max"


def is_dollars(value: object) -> bool:
    """Whether `value` is an amount a budget may be: a finite number above
    0, never a boolean."""
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and value > 0
    )


class Origin(StrEnum):
    """Where a value in force came from: the paper's file, the workspace's,
    the person's user file or shell (for the budget), the default, or (for
    the bibliography) the one `.bib` beside the paper."""

    PAPER = "paper"
    WORKSPACE = "workspace"
    USER = "user"
    SHELL = "shell"
    DEFAULT = "default"
    BESIDE = "beside"


def read_toml(path: Path) -> dict[str, Any]:
    """One settings file's tables; refused, naming the file, where it does
    not read."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SettingsError(f"{path}: cannot read it: {error}") from None


def workspace(paper: Path) -> tuple[Path, dict[str, Any]] | None:
    """The workspace a paper sits in, its root and its file's tables; None
    outside one."""
    paper = paper.resolve()
    for directory in paper.parents:
        candidate = directory / CONFIG_NAME
        if not candidate.is_file():
            continue
        data = read_toml(candidate)
        if "workspace" not in data:
            continue
        return (directory, data) if paper in _papers(directory, data) else None
    return None


def members(directory: Path) -> tuple[Path, tuple[Path, ...]] | None:
    """The workspace `directory` is the root of or a paper in: its root and
    its papers, in order; None outside one."""
    directory = directory.resolve()
    own = directory / CONFIG_NAME
    data = read_toml(own) if own.is_file() else {}
    if "workspace" in data:
        return directory, _papers(directory, data)
    found = workspace(directory)
    if found is None:
        return None
    return found[0], _papers(*found)


@dataclass(frozen=True)
class Here:
    """Where a directory stands: the paper it is in, and the workspace
    root it is in when it is in a workspace but in none of its papers
    (its root, a directory of its own)."""

    paper: Path | None
    workspace: Path | None = None
    #: `paper` is the directory itself, no paper being found above it
    guessed: bool = False


def here(start: Path, built: Callable[[Path], bool]) -> Here:
    """The paper `start` is in, found by walking up from it: the nearest
    directory that is a paper `built` (its store holds a record) or holds
    an `axiomation.toml` of a paper; a workspace's paper that holds it;
    else, in no workspace, `start` itself, a paper not yet built. At a
    workspace's root, or in a directory of the workspace outside its
    papers, no paper."""
    start = start.resolve()
    for directory in (start, *start.parents):
        own = directory / CONFIG_NAME
        data = read_toml(own) if own.is_file() else {}
        if "workspace" in data:
            held = (
                p for p in _papers(directory, data) if p == start or p in start.parents
            )
            paper = next(held, None)
            return Here(paper, None if paper is not None else directory)
        if own.is_file() or built(directory):
            return Here(directory)
    return Here(start, guessed=True)


def _papers(root: Path, data: Mapping[str, Any]) -> tuple[Path, ...]:
    pattern = str(data["workspace"].get("papers", DEFAULT_PAPERS))
    return tuple(sorted(p.resolve() for p in root.glob(pattern) if p.is_dir()))


@dataclass(frozen=True)
class PaperSettings:
    """A paper's settings as read: its directory, its own file's tables
    (empty where it has none), and its workspace's root and tables (None
    and empty outside one)."""

    paper: Path
    own: Mapping[str, Any] = field(default_factory=dict)
    workspace_root: Path | None = None
    shared: Mapping[str, Any] = field(default_factory=dict)

    @property
    def scope(self) -> Path:
        """What a setting may reach: the workspace, else the paper."""
        return self.paper if self.workspace_root is None else self.workspace_root

    def file(self, shared: bool) -> Path:
        """The file a section was read from: the workspace's or the paper's."""
        root = self.workspace_root if shared else self.paper
        assert root is not None
        return root / CONFIG_NAME

    def tables(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """A section as the paper's file and the workspace's hold it, each a
        table (empty where absent); refused where one is not a table."""
        found = []
        for shared, data in ((False, self.own), (True, self.shared)):
            table = data.get(name, {})
            if not isinstance(table, dict):
                raise SettingsError(f"{self.file(shared)}: [{name}] must be a table")
            found.append(table)
        return found[0], found[1]


def origin(read: PaperSettings, section: str, *keys: str) -> Origin:
    """Where a section's key (`runners`, `py` for `[computations.runners]`'s
    `py`) is in force from: the paper's file over the workspace's, else the
    default."""
    own, shared = read.tables(section)
    for table, found in ((own, Origin.PAPER), (shared, Origin.WORKSPACE)):
        for key in keys:
            table = table.get(key) if isinstance(table, dict) else None
        if table is not None:
            return found
    return Origin.DEFAULT


#: A file of `KEY=VALUE` lines other tools read for a project's secrets;
#: axiomation reads none, and names one it finds as ignored.
ENV_FILE = ".env"


def ignored_env_files(paper: Path, workspace_root: Path | None) -> tuple[Path, ...]:
    """The `.env` files beside the paper and at its workspace's root, which
    axiomation does not read: their values go in the user file."""
    found = (
        paper / ENV_FILE,
        None if workspace_root is None else workspace_root / ENV_FILE,
    )
    return tuple(dict.fromkeys(f for f in found if f is not None and f.is_file()))


def load(paper: Path) -> PaperSettings:
    """A paper's settings: its `axiomation.toml` and its workspace's;
    refused where either declares a person's machine fact or a secret."""
    paper = paper.resolve()
    own_path = paper / CONFIG_NAME
    own = read_toml(own_path) if own_path.is_file() else {}
    refuse_personal(own, own_path)
    found = workspace(paper)
    if found is None:
        return PaperSettings(paper, own)
    root, shared = found
    refuse_personal(shared, root / CONFIG_NAME)
    return PaperSettings(paper, own, root, shared)


#: The ends of a key's name that make it a secret.
_SECRET_ENDS = ("api_key", "token", "password", "secret")


def _user_key_named(name: str) -> UserKey | None:
    """The user file's key a settings file's key names: by its own name,
    its shell name or a service's bare name (`OCR_URL`); None for none,
    and for a key a paper may set too (the budget)."""
    from axiomation.user_settings import UserKey  # it imports this module

    lowered = name.lower()
    for key in UserKey:
        if key.paper_may_set:
            continue
        if lowered in (key.value, key.env.lower()):
            return key
    return None


def refuse_personal(data: Mapping[str, Any], file: Path, at: str = "") -> None:
    """Refused, naming the key and where it goes, where a settings file
    declares a person's machine fact or a secret, in any table."""
    from axiomation import user_settings  # it imports this module

    for name, value in data.items():
        where = f"{at}.{name}" if at else name
        found = _user_key_named(name)
        if found is not None:
            raise SettingsError(
                f"{file}: {where} is a fact of one person's machine, not the "
                f"paper's: {user_settings.where_to_put(found)}"
            )
        if name.lower().endswith(_SECRET_ENDS):
            raise SettingsError(
                f"{file}: {where} looks like a secret, and a paper's settings "
                "are shared with whoever has the paper: remove it. "
                f"axiomation's own keys go in {user_settings.path()}"
            )
        tables = value if isinstance(value, list) else [value]
        for table in tables:
            if isinstance(table, dict):
                refuse_personal(table, file, where)


def bibliography(read: PaperSettings) -> Path | None:
    """The paper's bibliography, as the cited works' settings resolve it:
    `[sources].bib`, the paper's over the workspace's, each relative to its
    own file; else `[paper].bib`; else the one `.bib` beside the paper.
    None where none is named and none or several lie beside it."""
    return bibliography_from(read)[0]


def bibliography_from(read: PaperSettings) -> tuple[Path | None, Origin]:
    """The paper's bibliography, as `bibliography` finds it, and where
    from: the default where none is found."""
    own, shared = read.tables("sources")
    for table, is_shared in ((own, False), (shared, True)):
        if "bib" in table:
            found = Origin.WORKSPACE if is_shared else Origin.PAPER
            return read.file(is_shared).parent / str(table["bib"]), found
    paper_table = read.own.get("paper", {})
    if isinstance(paper_table, dict) and "bib" in paper_table:
        return read.paper / str(paper_table["bib"]), Origin.PAPER
    beside = sorted(read.paper.glob("*.bib"))
    return (beside[0], Origin.BESIDE) if len(beside) == 1 else (None, Origin.DEFAULT)


def run_dollars_max(read: PaperSettings) -> tuple[float | None, Origin]:
    """The budget a run takes where no `--budget` is given, and where from:
    `[budget].run_dollars_max`, the paper's over the workspace's, over the
    person's own `run_dollars_max` (their shell's over their user file's):
    what one person may spend on any paper, which a paper or a workspace
    may set tighter or looser. None, the default, where none sets it.
    Refused where it is not a positive number, or the section holds
    another key."""
    own, shared = read.tables(BUDGET_SECTION)
    for table, is_shared in ((own, False), (shared, True)):
        unknown = sorted(set(table) - {RUN_DOLLARS_MAX})
        if unknown:
            raise SettingsError(
                f"{read.file(is_shared)}: [{BUDGET_SECTION}] holds only "
                f"{RUN_DOLLARS_MAX}, not {', '.join(unknown)}"
            )
    for table, is_shared in ((own, False), (shared, True)):
        if RUN_DOLLARS_MAX not in table:
            continue
        value = table[RUN_DOLLARS_MAX]
        if not is_dollars(value):
            raise SettingsError(
                f"{read.file(is_shared)}: [{BUDGET_SECTION}].{RUN_DOLLARS_MAX} "
                f"is dollars above 0, not {value!r}"
            )
        return float(value), Origin.WORKSPACE if is_shared else Origin.PAPER
    return _person_s_run_dollars_max()


def _person_s_run_dollars_max() -> tuple[float | None, Origin]:
    """The person's own budget default, from their shell or their user
    file; None, the default, where neither sets it."""
    from axiomation import user_settings  # it imports this module

    key = user_settings.UserKey.RUN_DOLLARS_MAX
    held = user_settings.load().get(key)
    if not held:
        return None, Origin.DEFAULT
    try:
        amount = float(held.value)
    except ValueError:
        amount = math.nan
    if not is_dollars(amount):
        where = (
            f"{key.env} in the shell"
            if held.origin is user_settings.UserOrigin.SHELL
            else f"{user_settings.path()}: {key.value}"
        )
        raise SettingsError(f"{where} is dollars above 0, not {held.value!r}")
    if held.origin is user_settings.UserOrigin.SHELL:
        return amount, Origin.SHELL
    return amount, Origin.USER
