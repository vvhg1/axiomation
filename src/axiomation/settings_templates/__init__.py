"""The settings files' templates: every setting the code reads, commented
out, each with what it does and its default, so a file written from one
changes nothing until a line is uncommented.

`config.toml` is the user file's (`user_settings.py`); `axiomation.toml`
a paper's (`settings.py`), and a workspace's with `workspace.toml`'s
`[workspace]` table before its first section. None sets a budget: a run
that asks a model needs `--budget` or a budget its owner wrote.
"""

from __future__ import annotations

import os
from enum import StrEnum
from importlib import resources
from pathlib import Path

from axiomation import settings, user_settings


class Template(StrEnum):
    """Which settings file a template is of."""

    USER = "user"
    PAPER = "paper"
    WORKSPACE = "workspace"


def text(kind: Template) -> str:
    """A template's text."""
    files = resources.files(__name__)
    if kind is Template.USER:
        return files.joinpath("config.toml").read_text(encoding="utf-8")
    paper = files.joinpath(settings.CONFIG_NAME).read_text(encoding="utf-8")
    if kind is Template.PAPER:
        return paper
    head, _, sections = paper.partition("\n[")
    table = files.joinpath("workspace.toml").read_text(encoding="utf-8")
    return f"{head}\n{table}[{sections}"


def target(kind: Template, directory: Path) -> Path:
    """The file a template is written to: the user file, or `directory`'s
    `axiomation.toml`."""
    if kind is Template.USER:
        return user_settings.path()
    return directory / settings.CONFIG_NAME


def write(kind: Template, directory: Path) -> Path:
    """The template written to its file, which must not exist: an existing
    file is refused, unchanged. The user file is readable by its owner
    alone, as it will hold keys."""
    path = target(kind, directory)
    if path.exists():
        raise settings.SettingsError(
            f"{path} exists; it is left as it is. To start again from the "
            "template, move it away first"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o600 if kind is Template.USER else 0o644
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text(kind))
    return path
