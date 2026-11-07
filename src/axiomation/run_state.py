"""A run's working state: how far a run has come, kept beside the store
so the same command resumes it, and removed once the whole state is kept
(`docs/architecture.md` § 4).

It is run data, not a store entry: no reader of the store reads it. It
holds the run's cursor and the texts of the answers the cursor still
stands on, by answer key, so a resumed run never reads an answer's text
out of the store."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from axiomation import develop

#: The directory, in the store's, that keeps each command's working
#: state, one file a command (`build.json`), so one command's run never
#: overwrites another's.
RUNS = "runs"
#: The file's format; a file of another is a run of other code, read as none.
FORMAT = 1


@dataclass(frozen=True, slots=True)
class Cursor:
    """Where a run stands: the version it runs on, the stage it reached
    and its pass or round there, the edges answer the run stands on and
    the person rounds applied, each by its answer's key. A run resumed
    applies its filed answers again by their keys; the cursor says how
    far it came."""

    version: str
    #: What the run's questions are made from: its settings, templates and
    #: code, hashed. A run made from others is no run to resume.
    made_from: str = ""
    stage: str = ""
    step: int = 0
    edges_answer: str | None = None
    person_rounds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunState:
    """A run's cursor and the texts it stands on, by answer key.

    An answer's text is written here before the answer is filed, and the
    cursor moves past it after: a run stopped between the two finds the
    answer filed and its text here, and applies it; one stopped before
    filing finds a text no filed answer has, and asks again."""

    command: str
    cursor: Cursor
    texts: Mapping[str, str] = field(default_factory=dict)

    def text(self, key: str) -> str:
        """The text of an answer the run stands on; a key the run holds no
        text for is a fault of the run, not of the store."""
        try:
            return self.texts[key]
        except KeyError:
            raise KeyError(f"the run holds no text for answer {key}") from None


def made_from(settings: object) -> str:
    """What a run's questions are made from, hashed: its settings, their
    templates' texts among them. A run made from others is not resumed."""
    return hashlib.sha256(repr(settings).encode("utf-8")).hexdigest()[:16]


def path(directory: Path, command: str) -> Path:
    return Path(directory) / RUNS / f"{command}.json"


def write(directory: Path, state: RunState) -> None:
    """The state written whole, through a temporary file renamed over the
    last: a crash leaves the last state or this one, never half of one."""
    data = json.dumps(_data(state), ensure_ascii=False, sort_keys=True)
    written: Path | None = None
    target = path(directory, state.command)
    target.parent.mkdir(exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent, suffix=".tmp", delete=False
        ) as file:
            written = Path(file.name)
            file.write(data)
        os.replace(written, target)
        written = None
    finally:
        if written is not None:
            written.unlink(missing_ok=True)


def read(
    directory: Path, command: str, version: str, made_from: str = ""
) -> RunState | None:
    """The working state of `command`'s run on `version` made from
    `made_from`, or none: no file, or another command's, version's,
    making's or format's run, is no run to resume."""
    try:
        data = json.loads(path(directory, command).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if data.get("format") != FORMAT:
        return None
    state = _from(data)
    if (
        state.command != command
        or state.cursor.version != version
        or state.cursor.made_from != made_from
    ):
        return None
    return state


def remove(directory: Path, command: str) -> None:
    """The working state dropped: the whole state is kept."""
    path(directory, command).unlink(missing_ok=True)


def _data(state: RunState) -> dict[str, Any]:
    cursor = state.cursor
    return {
        "format": FORMAT,
        "command": state.command,
        "cursor": {
            "version": cursor.version,
            "made_from": cursor.made_from,
            "stage": cursor.stage,
            "step": cursor.step,
            "edges_answer": cursor.edges_answer,
            "person_rounds": list(cursor.person_rounds),
        },
        "texts": dict(state.texts),
    }


def _from(data: Mapping[str, Any]) -> RunState:
    cursor: Mapping[str, Any] = data["cursor"]
    return RunState(
        command=data["command"],
        cursor=Cursor(
            version=cursor["version"],
            made_from=cursor["made_from"],
            stage=cursor["stage"],
            step=cursor["step"],
            edges_answer=cursor["edges_answer"],
            person_rounds=tuple(cursor["person_rounds"]),
        ),
        texts=dict(data["texts"]),
    )


#: The stages whose answers' texts a run holds: the edges sheets the
#: later passes, the carry and the rounds stand on, the review sheets
#: whose edge, group and inventory lines are given after filing, and an
#: identity answer's payload until its update keeps its step.
HELD = frozenset({"edges", "review", "identity"})


class RunFile:
    """A run's working state on disk, kept current as the run goes: open
    on `directory` for `command` on `version` made from `made_from`, it
    resumes the run there or starts one."""

    def __init__(
        self, directory: Path, command: str, version: str, made_from: str
    ) -> None:
        self.directory = Path(directory)
        self.state = read(directory, command, version, made_from) or RunState(
            command=command, cursor=Cursor(version=version, made_from=made_from)
        )

    def hold(self, key: str, stage: str, text: str) -> None:
        """An answer's text held before its answer is filed; a stage the
        run stands on no text of holds none."""
        if stage not in HELD:
            return
        self.state = RunState(
            self.state.command, self.state.cursor, {**self.state.texts, key: text}
        )
        write(self.directory, self.state)

    def moved(self, **cursor: Any) -> None:
        """The cursor moved past an applied answer."""
        self.state = RunState(
            self.state.command,
            replace(self.state.cursor, **cursor),
            self.state.texts,
        )
        write(self.directory, self.state)

    def text(self, key: str) -> str | None:
        return self.state.texts.get(key)

    def kept(self) -> None:
        """The whole state is kept: the run is done."""
        remove(self.directory, self.state.command)


class _Answered(Protocol):
    key: str
    attempt: int
    raw: str


def text_of(answer: _Answered, run: RunFile | None, directory: Path) -> str:
    """An answer's text as the run holds it, else as the answer in hand
    carries it (one just asked). An answer read from the record carries
    none: the record never holds one, so its run must, or, for a replay
    in develop mode, the develop file in `directory`, the store's
    (`develop.text`)."""
    return text_in(answer, {} if run is None else run.state.texts, directory)


def text_in(answer: _Answered, texts: Mapping[str, str], directory: Path | None) -> str:
    """An answer's text as `texts` (a run's, by key) hold it, else as the
    answer in hand carries it, else, in develop mode, as the develop file
    in `directory` (the store's) keeps it (`text_of`)."""
    held = texts.get(answer.key)
    if held is not None:
        return held
    if answer.raw:
        return answer.raw
    if directory is not None and develop.on():
        return develop.text(directory, answer.key, answer.attempt)
    raise develop.DevelopError(
        f"no run holds the text of answer {answer.key[:12]}: an answer filed "
        f"by an earlier run is read again only by a develop tool, with "
        f"{develop.ENV}=1"
    )
