"""A person's settings: the facts of their machine and their secrets, in
one user file, with the shell over it.

A paper's settings (`settings.py`) are shared with everyone who has the
paper. Where a service runs, the keys that open it, who asks the
lookups and where the person's PDFs are are the person's: they live in
`config.toml` in the person's axiomation directory
(`$XDG_CONFIG_HOME/axiomation`, else `~/.config/axiomation`), beside
their keys. The file holds flat keys
(`USER_KEYS`); the shell's `AXIOMATION_<KEY>` (`AXIOMATION_OCR_URL`)
speaks over each. No `.env` is read.

A file that holds a secret and that others may read is refused, as `ssh`
refuses a private key others can read.

Standard library only.
"""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from axiomation.settings import SettingsError, is_dollars

#: The user file's name in the person's axiomation directory.
FILE_NAME = "config.toml"
#: The prefix of a key's name in the shell.
ENV_PREFIX = "AXIOMATION_"


class UserKey(StrEnum):
    """A key of the user file."""

    QDRANT_URL = "qdrant_url"
    VOYAGE_API_KEY = "voyage_api_key"
    OCR_BACKEND = "ocr_backend"
    OCR_URL = "ocr_url"
    OCR_API_KEY = "ocr_api_key"
    OCR_MODEL = "ocr_model"
    CONTACT_EMAIL = "contact_email"
    CORE_API_KEY = "core_api_key"
    ZOTERO_URL = "zotero_url"
    PDF_DIR = "pdf_dir"
    #: "off": each cited work is read from its PDF, not the library, where
    #: the library would be read; a paper's or a workspace's `[sources]`
    #: may say so too, the shell's over theirs (`sources.config.resolve`).
    LIBRARY = "library"
    #: What a run may spend on any paper without `--budget`, in dollars:
    #: a number, the one key that is not a string.
    RUN_DOLLARS_MAX = "run_dollars_max"

    @property
    def env(self) -> str:
        """Its name in the shell: `AXIOMATION_OCR_URL`."""
        return ENV_PREFIX + self.value.upper()

    @property
    def secret(self) -> bool:
        """Whether it is a secret: shown as set or unset, never its value."""
        return self.value.endswith("_api_key")

    @property
    def paper_may_set(self) -> bool:
        """Whether a paper's or a workspace's file may set it too, over the
        user file's: the budget, which is no fact of their machine, and the
        library switch, where a paper's works are not in the library (the
        shell's switch over the paper's)."""
        return self in (UserKey.RUN_DOLLARS_MAX, UserKey.LIBRARY)


#: The value in force where the person sets none, for a key that has one.
#: The OCR address has none: a service runs where its person runs it.
#: Qdrant's has none: setting it, with the `[library]` extra, switches the
#: library on.
DEFAULTS: Mapping[UserKey, str] = {
    UserKey.ZOTERO_URL: "http://localhost:23119",
}


class UserSettingsError(SettingsError):
    """A user file that cannot be read, holds an unknown key, or holds a
    secret others may read."""


class UserOrigin(StrEnum):
    """Where a person's value in force came from."""

    SHELL = "shell"
    USER = "user"
    DEFAULT = "default"


@dataclass(frozen=True)
class UserValue:
    """A person's value and where it came from; empty, from the default,
    where none is set."""

    value: str = ""
    origin: UserOrigin = UserOrigin.DEFAULT

    def __bool__(self) -> bool:
        return bool(self.value)


def directory(environ: Mapping[str, str] | None = None) -> Path:
    """The person's axiomation directory: `axiomation` in
    `$XDG_CONFIG_HOME`, else in `~/.config`."""
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "axiomation"


def path(environ: Mapping[str, str] | None = None) -> Path:
    """The user file."""
    return directory(environ) / FILE_NAME


def where_to_put(key: UserKey) -> str:
    """Where a person sets `key`: the sentence an error ends with."""
    return f'set {key.value} = "…" in {path()}, or {key.env} in the shell'


def read_file(file: Path) -> dict[str, str]:
    """The user file's values; empty where there is none. Refused, naming
    the file, where it does not read, holds an unknown key or a value that
    is not a string, or holds a secret the group or others may read."""
    if not file.is_file():
        return {}
    try:
        with file.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise UserSettingsError(f"{file}: cannot read it: {error}") from None
    known = {k.value for k in UserKey}
    for name, value in data.items():
        if name not in known:
            raise UserSettingsError(
                f"{file}: {name} is no key of the user file "
                f"(known: {', '.join(sorted(known))}); a paper's settings go "
                "in its axiomation.toml"
            )
        if name == UserKey.RUN_DOLLARS_MAX:
            if not is_dollars(value):
                raise UserSettingsError(
                    f"{file}: {name} is dollars above 0, not {value!r}"
                )
        elif not isinstance(value, str):
            raise UserSettingsError(f"{file}: {name} must be a string")
    if os.name == "posix" and any(UserKey(n).secret and v for n, v in data.items()):
        mode = file.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise UserSettingsError(
                f"{file} holds a secret and others may read it: chmod 600 {file}"
            )
    return {name: str(value).strip() for name, value in data.items()}


@dataclass(frozen=True)
class UserSettings:
    """The person's values as read: the user file's path, its values, and
    the shell's of axiomation's names alone. Neither is printed: they
    hold the person's keys."""

    file: Path
    held: Mapping[str, str] = field(repr=False)
    environ: Mapping[str, str] = field(repr=False)

    def get(self, key: UserKey) -> UserValue:
        """A key's value in force: the shell's over the user file's."""
        shell = self.environ.get(key.env, "").strip()
        if shell:
            return UserValue(shell, UserOrigin.SHELL)
        held = self.held.get(key.value, "")
        if held:
            return UserValue(held, UserOrigin.USER)
        return UserValue()


def load(environ: Mapping[str, str] | None = None) -> UserSettings:
    """The person's settings: the user file and the shell."""
    source = os.environ if environ is None else environ
    file = path(source)
    shell = {key.env: source[key.env] for key in UserKey if key.env in source}
    return UserSettings(file, read_file(file), shell)


def get(key: UserKey) -> UserValue:
    """One key's value in force, read now."""
    return load().get(key)
