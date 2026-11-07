"""Develop mode: each filed answer's text kept beside the record, for the
tools that read an answer again (`docs/architecture.md` § 4).

The record never holds an answer's text. With `AXIOMATION_DEVELOP=1` set,
the resolver appends each one to `develop.jsonl` in the store's directory,
one line an attempt; without it, the text goes nowhere once its answer is
applied."""

from __future__ import annotations

import json
import os
from pathlib import Path

#: The variable that turns develop mode on.
ENV = "AXIOMATION_DEVELOP"
#: The file, in the store's directory, that develop mode appends to.
FILE = "develop.jsonl"


class DevelopError(Exception):
    """A develop tool asked for a text develop mode did not keep."""


def on() -> bool:
    return os.environ.get(ENV) == "1"


def keep(directory: Path, key: str, attempt: int, text: str) -> None:
    """An answer's text appended, in develop mode only."""
    if not on():
        return
    line = json.dumps(
        {"key": key, "attempt": attempt, "text": text}, ensure_ascii=False
    )
    with (Path(directory) / FILE).open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def text(directory: Path, key: str, attempt: int | None = None) -> str:
    """The text of answer `key`'s attempt `attempt` (its latest where
    none is named), as develop mode kept it."""
    found: str | None = None
    try:
        lines = (Path(directory) / FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        held = json.loads(line)
        if held["key"] == key and (attempt is None or held["attempt"] == attempt):
            found = held["text"]
    if found is None:
        raise DevelopError(
            f"no text kept for answer {key[:12]}"
            + ("" if attempt is None else f" attempt {attempt}")
            + f": the record never holds one; only a run with {ENV}=1 "
            f"keeps it, in {FILE}"
        )
    return found
