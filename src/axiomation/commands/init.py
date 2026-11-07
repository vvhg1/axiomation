"""`init`: a settings file written from its template, every setting
commented out and explained (`settings_templates`): the person's user
file, or a paper's or a workspace's `axiomation.toml` in a directory
given or the current one. It needs no paper, so it takes none first."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation import settings_templates
from axiomation.commands import Say, UsageError, requested
from axiomation.settings_templates import Template


@dataclass(frozen=True)
class Init:
    """`init`: which settings file to write, and where for a paper's or a
    workspace's."""

    kind: str
    directory: Path = Path(".")


def request(arguments: argparse.Namespace) -> Init:
    """`init`'s request from its parsed arguments."""
    return requested(Init, arguments)


def run(asked: Init, say: Say) -> int:
    """The template written; an existing file refused, unchanged."""
    kind = Template(asked.kind)
    if kind is not Template.USER and not asked.directory.is_dir():
        raise UsageError(f"{asked.directory} is no directory")
    if kind is Template.USER and asked.directory != Path("."):
        raise UsageError("the user file has its own place: give no directory")
    written = settings_templates.write(kind, asked.directory)
    say.line(
        f"wrote {written}: every setting is commented out, with what it does; "
        'remove a line\'s "# " to set it'
    )
    return 0


def _init_command(commands: Any) -> None:
    found = commands.add_parser(
        "init",
        help="write a settings file with every setting commented out and "
        "explained; an existing file is left as it is",
    )
    found.add_argument(
        "kind",
        choices=[t.value for t in Template],
        help="user: your config.toml, beside your keys; paper or workspace: "
        "DIRECTORY's axiomation.toml",
    )
    found.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path("."),
        help="where a paper's or a workspace's file goes (default: here)",
    )


#: `init`'s arguments.
ARGUMENTS: dict[str, Callable[[Any], None]] = {"init": _init_command}
