"""What every gate shares (docs/architecture.md § 2): its rounds, its log,
the reason it hands back, and the files it reads and writes.

A gate runs outside the session's sandbox, as the host's Python, on files
the session can reach: its spec, the session's file, its round count and
its log lie where the session can write. So it reads and writes none of
them through a link, nor any but a regular file: a link the session
planted would reach a file the sandbox closes (an answer key), or let the
gate overwrite one.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from axiomation.questions import GATE_LOG, PROBLEMS_SHOWN

#: How often a gate sends a session back at its end before it lets it end;
#: the runner's check and re-ask take over after.
ROUNDS = 3
#: The gate's count of rounds, beside its spec.
TRIES = "gate-tries"
#: The last file a check on a write read, beside its spec.
LAST_CHECKED = "gate-last-checked"

#: Opening a file never follows a link where the platform can refuse one,
#: and never waits on a pipe the session planted in its place.
NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


class NotRegularFile(OSError):
    """A file a session could have planted as a link, or as anything but a
    regular file, where code outside its sandbox reads or writes it."""


def _held(path: Path, handle: int) -> None:
    """Refuse an opened file that is not a regular file of one name: a
    pipe, a device, or a hard link to a file the sandbox closes."""
    found = os.fstat(handle)
    if not stat.S_ISREG(found.st_mode) or found.st_nlink != 1:
        raise NotRegularFile(f"{path.name} is not a regular file of its own")


def read_regular(path: Path) -> str:
    """A file's text, refusing a link and anything but a regular file."""
    try:
        handle = os.open(path, os.O_RDONLY | NO_FOLLOW)
    except OSError as error:
        raise NotRegularFile(f"{path.name} is not a file to read ({error})") from None
    with os.fdopen(handle, "rb") as given:
        _held(path, given.fileno())
        return given.read().decode("utf-8")


def write_regular(path: Path, text: str, append: bool = False) -> None:
    """`text` written to `path`, or appended, never through a link; a file
    refused is left as it was (it is emptied only once it is held)."""
    flags = os.O_WRONLY | os.O_CREAT | NO_FOLLOW | (os.O_APPEND if append else 0)
    try:
        handle = os.open(path, flags, 0o600)
    except OSError as error:
        raise NotRegularFile(f"{path.name} is not a file to write ({error})") from None
    with os.fdopen(handle, "w", encoding="utf-8") as given:
        _held(path, given.fileno())
        if not append:
            os.ftruncate(given.fileno(), 0)
        given.write(text)


def own_directory(path: Path) -> bool:
    """Whether `path` is a directory of its own: no link, which opening a
    file inside it would follow."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def directory(path: Path) -> Path:
    """`path`, a directory made where none is, refusing a link or anything
    but a directory there: the gate writes into it."""
    if os.path.lexists(path) and not own_directory(path):
        raise NotRegularFile(f"{path.name} is not a directory of its own")
    path.mkdir(exist_ok=True)
    return path


def hook_input() -> None:
    """Claude Code's hook input, read and left: no gate needs it."""
    with contextlib.suppress(OSError, ValueError):
        sys.stdin.read()


def spec(path: Path) -> Any:
    """A gate's spec, as its host wrote it."""
    return json.loads(read_regular(path))


def round_left(spec_path: Path) -> bool:
    """Whether the gate may send the session back once more, the round
    counted: fewer than `ROUNDS` so far. A gate that cannot count would
    never let go, so it lets go."""
    tries = spec_path.with_name(TRIES)
    count = 0
    with contextlib.suppress(OSError, ValueError):
        count = int(read_regular(tries))
    if count >= ROUNDS:
        return False
    try:
        write_regular(tries, str(count + 1))
    except OSError:
        return False
    return True


def unchanged(spec_path: Path, edited: str) -> bool:
    """Whether a check on a write read this same file last: it need not run
    again. The file's digest is kept beside the spec."""
    last = spec_path.with_name(LAST_CHECKED)
    digest = hashlib.sha256(edited.encode("utf-8")).hexdigest()
    with contextlib.suppress(OSError):
        if read_regular(last) == digest:
            return True
    with contextlib.suppress(OSError):
        write_regular(last, digest)
    return False


def log(spec_path: Path, entry: Mapping[str, Any]) -> None:
    """One line per check that ran, beside the spec (`GATE_LOG`), which the
    host adds to the attempt's transcript."""
    with contextlib.suppress(OSError):
        write_regular(
            spec_path.with_name(GATE_LOG),
            json.dumps(entry, ensure_ascii=False) + "\n",
            append=True,
        )


def reason(
    edited: str,
    problems: Sequence[str],
    then: str = "then finish",
    most: int = PROBLEMS_SHOWN,
) -> str:
    """What a gate hands back: the file that does not read yet and its
    problems, the first `most` of them."""
    shown = list(problems[:most])
    if len(problems) > most:
        shown.append(f"and {len(problems) - most} more")
    listed = "\n".join(f"- {problem}" for problem in shown)
    return (
        f"{edited} does not read yet:\n\n{listed}\n\n"
        f"Fix these in {edited}, keep the rest, {then}."
    )


def block(said: str) -> None:
    """Claude Code's `block` decision: the session goes on, told `said`."""
    print(json.dumps({"decision": "block", "reason": said}))
