"""What kept states and records named by their content give, kept for this
user (`docs/architecture.md` § 4): neither ever changes, so neither does
what the same code computes from them. Each value is kept in the user's
cache (`AXIOMATION_CACHE`, else `$XDG_CACHE_HOME/axiomation`, else
`~/.cache/axiomation`), never in a paper or its store, so nothing derived
travels with a paper: a value read here was written here, by this user.
Values are named by what they come from (a state's id, a blob's name), so
none goes stale; a directory named by the code that derived them holds
them, one compressed file a value. A value missing, unreadable or derived
by other code is computed again, and the cache can be removed at any
time: a command answers the same without it, slower. The values written
last are kept up to `CACHE_BYTES_MAX`.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import pickle
import stat
import tempfile
import zlib
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

#: The environment variable naming the cache's directory.
CACHE_VARIABLE = "AXIOMATION_CACHE"
SUFFIX = ".pickle.z"
#: zlib's fastest level: a value is written once and read by every command,
#: and the fastest shrinks one about five times.
COMPRESSION_LEVEL = 1
#: The cache's bound: past it, the values used longest ago go first.
CACHE_BYTES_MAX = 1024 * 1024 * 1024

_PACKAGE = Path(__file__).parent


def _code() -> str:
    """The package as this process loaded it: its files' paths and contents
    (its briefs and tables too), hashed. Any change derives every value
    again."""
    digest = hashlib.sha256()
    for path in sorted(_PACKAGE.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(_PACKAGE)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


#: Read at import: what the process runs, whatever is edited on disk later.
CODE = _code()


def root() -> Path:
    """The user's cache for axiomation."""
    named = os.environ.get(CACHE_VARIABLE)
    if named:
        return Path(named)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "axiomation"


def _directory() -> Path:
    return root() / "derived" / CODE


@cache
def _trusted(directory: Path) -> bool:
    """Whether `directory` and the one holding it are this user's alone:
    owned by them, written by no one else. A value is read only from such
    a directory."""
    for held in (directory, directory.parent):
        try:
            found = held.stat()
        except OSError:
            return False
        if found.st_uid != os.getuid() or found.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return False
    return True


def names() -> frozenset[str]:
    """The values kept under this code, by name."""
    directory = _directory()
    if not _trusted(directory):
        return frozenset()
    try:
        return frozenset(
            p.name.removesuffix(SUFFIX) for p in directory.glob(f"*{SUFFIX}")
        )
    except OSError:
        return frozenset()


def load(name: str) -> Any | None:
    """The value kept as `name`, None where none is kept or it cannot be
    read (a value is computed again, never refused)."""
    directory = _directory()
    if not _trusted(directory):
        return None
    path = directory / f"{name}{SUFFIX}"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        found = pickle.loads(zlib.decompress(data))
    except Exception:  # noqa: BLE001 - a torn or foreign value is computed again
        return None
    with contextlib.suppress(OSError):
        os.utime(path)  # used: the bound removes it last
    return found


def save(name: str, value: Any) -> None:
    """Keep `value` as `name`, replacing it whole, so a reader never meets
    half of one. A value that cannot be kept (a cache that cannot be
    written, a value pickle cannot write) is not kept, and its reads are
    computed again."""
    try:
        data = zlib.compress(
            pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL), COMPRESSION_LEVEL
        )
    except (pickle.PicklingError, TypeError, AttributeError):
        return
    directory = _directory()
    written: Path | None = None
    try:
        fresh = not directory.is_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if fresh:
            for made in (root() / "derived", directory):
                made.chmod(0o700)
            _trusted.cache_clear()
        if not _trusted(directory):
            return
        with tempfile.NamedTemporaryFile(
            "wb", dir=directory, suffix=".tmp", delete=False
        ) as file:
            written = Path(file.name)
            file.write(data)
        os.replace(written, directory / f"{name}{SUFFIX}")
        written = None
        _bound_once()
    except OSError:
        if written is not None:
            written.unlink(missing_ok=True)


def held[T](name: str, compute: Callable[[], T]) -> T:
    """The value kept as `name`, else `compute`'s, kept."""
    found = load(name)
    if found is None:
        found = compute()
        save(name, found)
    return found


@cache
def _bound_once() -> None:
    """The cache kept within `CACHE_BYTES_MAX`, once a process that writes
    to it: the values used longest ago removed first, whichever code
    derived them."""
    try:
        found = [
            (p.stat().st_mtime_ns, p.stat().st_size, p)
            for p in (root() / "derived").rglob(f"*{SUFFIX}")
        ]
    except OSError:
        return
    total = sum(size for _, size, _ in found)
    for _, size, path in sorted(found):
        if total <= CACHE_BYTES_MAX:
            break
        path.unlink(missing_ok=True)
        total -= size
    for directory in (root() / "derived").iterdir():
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
