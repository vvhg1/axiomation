"""The paper's directory as a command's first argument, which may be left
out: the paper the current directory is in, put into the command line
before it is parsed. Neither a command nor a front end, so both the
command line and `axiomation sources`' take it (docs/architecture.md
§ 1)."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from axiomation import records, settings, versions
from axiomation.reader.includes import STORE_DIRECTORY

#: What a left-out directory defaults to, in each command's help.
DIRECTORY_DEFAULT = "default: the paper the current directory is in"
#: `--store`'s help, the same on every command.
STORE_HELP = f"the paper's store (default: PAPER/{STORE_DIRECTORY}/)"
#: How many of a workspace's papers a refusal names.
PAPERS_NAMED = 3


class PaperNotFound(ValueError):
    """No paper where the command runs, and none given."""


@dataclass(frozen=True)
class Given:
    """The command line with its paper, and the directory taken as a paper
    where no paper was found above it (`settings.Here.guessed`), to say."""

    argv: list[str]
    guessed: Path | None = None


def directory_argument(command: argparse.ArgumentParser) -> None:
    """The paper's directory, the command's first argument, which may be
    left out (`with_paper`)."""
    command.add_argument(
        "directory",
        type=Path,
        metavar="PAPER",
        help=f"the paper's directory ({DIRECTORY_DEFAULT})",
    )
    command.set_defaults(paper_first=True)


def with_paper(
    parser: argparse.ArgumentParser, argv: list[str], workspace: bool = False
) -> Given:
    """`argv`, its command's paper put in where it takes the paper first
    and leaves it out (`_left_out`): the paper the current directory is
    in, found by walking up (`settings.here`). At a workspace's root, or
    in its directories outside its papers, the root where `workspace` (a
    command that reads the workspace's corpus), else refused
    (`PaperNotFound`), naming its papers. Asked for help, as given."""
    command = _subparser(parser, argv[0]) if argv else None
    if command is None or not command.get_default("paper_first"):
        return Given(argv)
    rest = argv[1:]
    words, asks_help = _positionals(command, rest)
    if asks_help or not _left_out(command, words):
        return Given(argv)
    try:
        found = settings.here(
            Path.cwd(), lambda d: records.holds_record(versions.store_of(d))
        )
    except settings.SettingsError as error:
        raise PaperNotFound(f"finding the paper from here: {error}") from None
    paper = found.paper if found.paper is not None or not workspace else found.workspace
    if paper is None:
        assert found.workspace is not None
        held = settings.members(found.workspace)
        named = [os.path.relpath(p, found.workspace) for p in (held[1] if held else ())]
        some = ", ".join(named[:PAPERS_NAMED]) + (
            ", …" if len(named) > PAPERS_NAMED else ""
        )
        raise PaperNotFound(
            f"{found.workspace} is a workspace, in none of its papers: give the "
            f"paper's directory first (`axiomation {argv[0]} PAPER …`"
            + (f"; its papers: {some}" if named else "")
            + "), or run the command inside the paper"
        )
    return Given([argv[0], str(paper), *rest], paper if found.guessed else None)


def _left_out(command: argparse.ArgumentParser, words: list[str]) -> bool:
    """Whether the positional `words` leave the paper out. Where every
    positional after it is required, by their count: one word fewer than
    the paper and they take, unless the first is a paper (its store's
    record or its `axiomation.toml`: the argument after it forgotten); a
    mistyped path is then still the paper, refused as such. Where one is
    optional or a list, by the first word: no existing directory and no
    path (no separator, not `.` or `..`)."""
    after = [a for a in command._actions if not a.option_strings][1:]
    first = words[0] if words else ""
    if all(a.nargs is None or isinstance(a.nargs, int) for a in after):
        taken = sum(1 if a.nargs is None else int(a.nargs) for a in after)
        return len(words) == taken and not (first and _is_paper(Path(first)))
    return not (
        (first and Path(first).is_dir()) or "/" in first or first in (".", "..")
    )


def _is_paper(directory: Path) -> bool:
    """Whether `directory` is a paper: its store holds a record, or it
    holds an `axiomation.toml`."""
    return (
        records.holds_record(versions.store_of(directory))
        or (directory / settings.CONFIG_NAME).is_file()
    )


def _subparser(
    parser: argparse.ArgumentParser, name: str
) -> argparse.ArgumentParser | None:
    """`parser`'s command `name`'s own parser; None where it has none."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and name in action.choices:
            return action.choices[name]
    return None


def _positionals(
    command: argparse.ArgumentParser, words: list[str]
) -> tuple[list[str], bool]:
    """The positional words of `words`, none an option or an option's
    value as `command`'s options take them, up to a `--`; and whether
    help is asked."""
    options = command._option_string_actions
    found: list[str] = []
    at = 0
    while at < len(words):
        word = words[at]
        if word == "--":
            break
        at += 1
        if not word.startswith("-") or word == "-":
            found.append(word)
            continue
        name = word.split("=", 1)[0]
        action = options.get(name) or next(
            (a for o, a in options.items() if o.startswith(name)), None
        )
        if isinstance(action, argparse._HelpAction):
            return found, True
        if action is None or "=" in word or action.nargs == 0:
            continue
        if action.nargs in (None, 1, "?"):
            given = at < len(words) and not words[at].startswith("-")
            if action.nargs != "?" or given:
                at += 1
        elif isinstance(action.nargs, int):
            at += action.nargs
        else:
            while at < len(words) and not words[at].startswith("-"):
                at += 1
    return found, False
