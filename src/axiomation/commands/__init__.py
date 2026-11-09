"""Each `axiomation` command as a function on a typed request.

A module per family of commands holds their arguments, the request each
command takes (its fields its flags' destinations), the function from
the parsed arguments to it, the command's function and its output. A
front end (the command line, the viewer's server) builds a request and
calls the command; no component imports a command (docs/architecture.md
§ 1). What a command says while it runs goes to the `Say` it is given,
never to the process's streams directly: a front end may own them (an
MCP server's protocol runs on stdout).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import IntEnum
from pathlib import Path
from typing import Any

from axiomation import versions
from axiomation.cutter import DisplayRule
from axiomation.paper_argument import STORE_HELP as STORE_HELP
from axiomation.paper_argument import directory_argument
from axiomation.records import RecordStore
from axiomation.versions import State


class UsageError(Exception):
    """A request a command cannot run with; its words name the flags, the
    product's vocabulary for a command's options."""


class Exit(IntEnum):
    """How a command ended, as the command line's exit code."""

    OK = 0
    #: refused, or a gate fails (`check`)
    FAILED = 1
    #: a run stopped before it was whole: its budget, a failure
    STOPPED = 3
    #: a session touched a path outside its tools
    BREACH = 4
    #: a question is unanswered: nothing kept; the same command resumes
    UNANSWERED = 5


class Say:
    """Where a command's lines go as it runs: by default the process's
    streams, looked up at each call."""

    def line(self, text: str, flush: bool = False) -> None:
        """A line of what the command reports."""
        print(text, flush=flush)

    def note(self, text: str) -> None:
        """A line beside the report: a note, a warning, a refusal."""
        print(text, file=sys.stderr)

    def write(self, text: str) -> None:
        """A result written whole, as it is."""
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.flush()


def written(text: str, output: Path | None, say: Say) -> None:
    """A result to the file `-o` names, the command's own write; said whole
    where none is named."""
    if output is None:
        say.write(text)
    else:
        output.write_bytes(text.encode("utf-8"))


@dataclass(frozen=True)
class Ran:
    """How a run ended, and, for a dry run that priced it, its round's
    estimate and expected cost."""

    code: int
    priced: tuple[float, float] | None = None


@dataclass(frozen=True)
class Paper:
    """A paper, the store that keeps its states, and the state a command
    reads (None: the latest)."""

    directory: Path
    store: Path
    state: str | None = None


def own_store(paper: Paper) -> None:
    """The paper's own store made with its `.gitignore`, where the run
    uses it."""
    own = versions.store_of(paper.directory)
    if Path(paper.store).resolve() == own.resolve():
        versions.default_store(paper.directory)


def state_summary(paper: Paper, store: RecordStore, kept: State) -> dict[str, Any]:
    """The state a run kept: its id and parent, the heads where
    there are several, and the paper's own store where `--store` named
    another."""
    heads = versions.heads(versions.states(store))
    own = versions.store_of(paper.directory)
    return {
        "id": kept.id,
        "parent": kept.parent,
        **({"heads": [h.id for h in heads]} if len(heads) > 1 else {}),
        **(
            {"paper_store_not_used": str(own)}
            if versions.carries_store(paper.directory)
            and own.resolve() != Path(paper.store).resolve()
            else {}
        ),
    }


def kept_store(paper: Paper) -> RecordStore:
    """The paper's store, refused where it keeps no state."""
    if not versions.keeps_state(paper.store):
        raise UsageError(f"{paper.store} keeps no state: build the paper first")
    return RecordStore(paper.store)


def chosen(paper: Paper, store: RecordStore, waiting: bool = False) -> State:
    """The state the paper names, else the store's latest; `waiting`: the
    latest of those that wait for the person's word too, which their next
    edit builds on."""
    if paper.state is not None:
        return versions.state(store, paper.state, waiting)
    found = versions.latest(store, waiting)
    assert found is not None
    return found


def kept_on(paper: Paper, store: RecordStore) -> State:
    """The state a command that keeps one builds on (`chosen`), a state
    waiting for the person's word among them: what it keeps waits with
    it. Refused while joined copies' states stand apart, whichever is
    named: nothing is kept on top of them until their work is merged
    (`docs/architecture.md` § 4, "Copies joined"); and where a state named
    is not the one waiting (`versions.refuse_beside_waiting`). Every such
    command takes its state here first, before it asks, files or pays."""
    versions.refuse_apart(store)
    found = chosen(paper, store, waiting=True)
    versions.refuse_beside_waiting(store, found)
    return found


@contextmanager
def scratch_copy(paper: Paper) -> Iterator[Paper]:
    """`paper` on a copy of its store, its transcripts left out, for as
    long as it is open: what is given there is gone with it."""
    if not versions.keeps_state(paper.store):
        raise UsageError(f"{paper.store} keeps no state: build the paper first")
    source = RecordStore(Path(paper.store))
    with tempfile.TemporaryDirectory(prefix="axiomation-dry-") as directory:
        copy = source.copy_to(Path(directory) / "store")
        try:
            yield replace(paper, store=copy.directory)
        finally:
            RecordStore(copy.directory).forget_tip()


def requested[Request](
    kind: type[Request], arguments: argparse.Namespace, **given: Any
) -> Request:
    """The request `kind` from a command's parsed arguments: each field the
    value of the flag of its name (its destination), a repeated flag's as a
    tuple; `given` the fields no flag gives (the paper)."""
    found: dict[str, Any] = {}
    for field in dataclasses.fields(kind):  # type: ignore[arg-type]
        if field.name in given:
            found[field.name] = given[field.name]
        elif hasattr(arguments, field.name):
            value = getattr(arguments, field.name)
            found[field.name] = _tupled(value)
    return kind(**found)


def _tupled(value: Any) -> Any:
    """A list, and each list in it (a repeated `nargs=2` flag's), as a tuple."""
    return tuple(_tupled(v) for v in value) if isinstance(value, list) else value


def paper_of(arguments: argparse.Namespace) -> Paper:
    """The paper, its store and the state a command's arguments name."""
    return Paper(
        Path(arguments.directory),
        Path(arguments.store),
        getattr(arguments, "state", None),
    )


def given_export(path: Path) -> dict[str, Any]:
    """The exchange file `--cut` gives."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise UsageError(f"--cut {path} is not JSON: {error}") from None
    if not isinstance(payload, dict):
        raise UsageError(f"--cut {path} is not an exchange file")
    return payload


def paper_arguments(command: argparse.ArgumentParser) -> None:
    directory_argument(command)
    command.add_argument(
        "--root", type=Path, help="the root file, relative to the directory"
    )


#: `--dry-run`'s help on a command that asks agents.
DRY_RUN_HELP = "price the run and stop: ask nothing, keep nothing"
SUMMARY_JSON_HELP = (
    "the whole summary as JSON, not lines; with --dry-run, the whole pricing"
)


def dollars(amount: float) -> str:
    """A price as a person reads it: cents, and a price under a cent so."""
    return "under $0.01" if 0 < amount < 0.005 else f"${amount:.2f}"


#: The steps a build or an update may end with, in the order they run.
FOLLOW_UPS = ("reread", "support", "place", "circles")


def summary_lines(summary: Mapping[str, Any]) -> str:
    """A build's or an update's summary as a person reads it: the state
    kept or why none was, the questions, what the graph or the reach came
    to, each step it ended with, and what it spent; `--json` gives the
    whole."""
    lines = [_state_words(summary)]
    asked = summary.get("questions") or {}
    if asked:
        answered = summary.get("answered")
        counts = ", ".join(f"{stage} {n}" for stage, n in asked.items())
        left = len(summary.get("unanswered") or ())
        if isinstance(answered, Mapping):
            left = sum(asked.values()) - sum(answered.values())
        lines.append(
            f"  questions: {counts}; "
            + (f"{left} unanswered" if left else "all answered")
        )
    for failed in summary.get("failed") or ():
        if isinstance(failed, Mapping):
            failed = f"{failed['key']} ({failed['stage']})"
        lines.append(f"  failed: {failed}")
    if "vocabulary" in summary and isinstance(summary["vocabulary"], Mapping):
        left = summary["vocabulary"]["unanswered"]
        lines.append(f"  the vocabulary map: {len(left)} questions unanswered")
    if isinstance(summary.get("edges"), Mapping):
        edges = summary["edges"]
        lines.append(
            f"  graph: {edges['edges']} edges, {edges['standings']} standings, "
            f"{edges['self_marks']} self marks; {summary.get('applied', 0)} "
            "corrections applied"
            + (f", {summary['problems']} problems" if summary.get("problems") else "")
        )
    if isinstance(summary.get("reach"), Mapping):
        reach = summary["reach"]
        words = [f"{reach['asked']} blocks asked"]
        if "open_marks" in reach:
            words.append(f"{reach['open_marks']} stale marks open")
        if reach.get("closed_by_delta"):
            words.append(f"{reach['closed_by_delta']} closed by their Δ")
        if reach.get("moved_by_rule_unread"):
            words.append(f"{reach['moved_by_rule_unread']} carried unread")
        lines.append("  reach: " + ", ".join(words))
    if summary.get("lost"):
        lines.append(f"  edges lost with their blocks: {len(summary['lost'])}")
    for step in FOLLOW_UPS:
        found = summary.get(step)
        if isinstance(found, Mapping):
            lines.append(f"  {step}: {_counts_words(found)}")
    lines.append(
        _spent_words(summary["spent"])
        if "spent" in summary
        else "(--json gives the whole summary)"
    )
    if summary.get("stopped"):
        detail = summary.get("detail") or ""
        lines.append(
            f"stopped: {summary['stopped']}" + (f": {detail}" if detail else "")
        )
    return "\n".join(lines)


def _state_words(summary: Mapping[str, Any]) -> str:
    """The state a run kept, from what, and the heads beside it; or that
    it kept none, and why."""
    if summary.get("built"):
        return f"state {summary['state']['id']} of this version is kept already"
    state = summary.get("state")
    if not isinstance(state, Mapping):
        why = "stopped" if summary.get("stopped") else "not whole"
        return f"no state kept: {why}; the same command resumes"
    words = f"state {state['id']} kept"
    if state.get("parent"):
        words += f", on {state['parent']}"
    if state.get("heads"):
        words += f"; the heads apart: {', '.join(state['heads'])}"
    if state.get("paper_store_not_used"):
        words += f"; the paper's own store, not used: {state['paper_store_not_used']}"
    return words


def _counts_words(found: Mapping[str, Any]) -> str:
    """A step's summary as its counts and its state, its spend apart."""
    words = [
        f"{value} {name.replace('_', ' ')}"
        for name, value in found.items()
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    words += [
        f"{len(value)} {name.replace('_', ' ')}"
        for name, value in found.items()
        if isinstance(value, list) and value
    ]
    words += [
        f"{name.replace('_', ' ')}: {value}"
        for name, value in found.items()
        if isinstance(value, str) and name != "state"
    ]
    if found.get("state"):
        words.append(f"state {found['state']}")
    if found.get("spent"):
        words.append(dollars(found["spent"]))
    return ", ".join(words) or "nothing to do"


def _spent_words(spent: float | Mapping[str, float]) -> str:
    """What the run spent; for a build, the store's answers in all too."""
    if isinstance(spent, Mapping):
        return (
            f"spent {dollars(spent['run'])}"
            + (
                f"; the store's answers, {dollars(spent['store'])} in all"
                if "store" in spent
                else ""
            )
            + " (--json gives the whole summary)"
        )
    return f"spent {dollars(spent)} (--json gives the whole summary)"


def said(summary: Mapping[str, Any], as_json: bool) -> str:
    """A run's summary, as JSON or as lines."""
    return json.dumps(summary, indent=2) if as_json else summary_lines(summary)


def questions_words(found: Mapping[str, Any]) -> str:
    """A priced stage's questions, and how many are answered already."""
    count, answered = found["questions"], found.get("answered", 0)
    words = f"{count} question" + ("" if count == 1 else "s")
    return words + (f", {answered} answered" if answered else "")


def prompts_argument(command: argparse.ArgumentParser, with_ask: bool = False) -> None:
    """`--prompts DIR` on a command that asks agents: with or without
    `--dry-run`, what each would be served written under DIR; `with_ask`:
    the command asks only with `--ask`."""
    command.add_argument(
        "--prompts",
        type=Path,
        metavar="DIR",
        help=("with --ask: " if with_ask else "")
        + "write what each agent is served under DIR",
    )


def output_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "-o", "--output", type=Path, help="the file to write; stdout without it"
    )


def displays_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--displays",
        choices=[rule.value for rule in DisplayRule],
        default=DisplayRule.OWN_BLOCK.value,
        help="a display is its own block, or part of the prose it touches",
    )


def kept_arguments(command: argparse.ArgumentParser) -> None:
    """A read of the kept states: the paper, its store, and the state."""
    directory_argument(command)
    command.add_argument("--store", type=Path, help=STORE_HELP)
    command.add_argument(
        "--state", help="a state's id or a prefix of it (default: the latest)"
    )
