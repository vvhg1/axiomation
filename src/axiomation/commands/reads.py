"""The reads: a paper's layer and its cut, and the bounded reads over its
kept states (`queries`, `check`, `inbox`) as the command line and the
viewer's server ask them, each read's flags its request's fields."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation import check as checks
from axiomation import computations, inbox, judgments, queries, user_settings, versions
from axiomation.commands import (
    STORE_HELP,
    Exit,
    Paper,
    UsageError,
    chosen,
    displays_argument,
    kept_arguments,
    kept_store,
    output_argument,
    paper_arguments,
    paper_of,
    requested,
)
from axiomation.commands.update import made_identity
from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.cutter.correspondence import correspond
from axiomation.driver import Templates, map_vocabulary
from axiomation.exchange import (
    cut_from_payload,
    cut_payload,
    document_payload,
    dumps,
    vocabulary_of,
)
from axiomation.paper_argument import directory_argument
from axiomation.questions import edge_sheet as es
from axiomation.questions.edge_sheet import KINDS, ROLES, STATUSES, sheet_files
from axiomation.reader import ReaderError, Vocabulary, document_hash, read_paper
from axiomation.reader.includes import find_root, walk
from axiomation.reader.layer import read_layer
from axiomation.records import JUDGED_ASPECTS, RecordStore
from axiomation.settings import (
    BUDGET_SECTION,
    CONFIG_NAME,
    RUN_DOLLARS_MAX,
    bibliography_from,
    ignored_env_files,
    origin,
    run_dollars_max,
)
from axiomation.settings import load as load_settings
from axiomation.update import UpdateSettings, planned
from axiomation.user_settings import UserKey
from axiomation.versions import State, VersionError

#: A kept state read for queries; the viewer's server keeps one per state.
ViewOf = Callable[[RecordStore, State], queries.View]


@dataclass(frozen=True)
class Document:
    """`read` and `cut`: a paper's layer, and its cut where `displays` is
    given."""

    directory: Path
    root: Path | None = None
    displays: str | None = None


@dataclass(frozen=True)
class Compare:
    first: Path
    second: Path
    displays: str = DisplayRule.OWN_BLOCK.value
    json: bool = False


@dataclass(frozen=True)
class States:
    paper: Paper
    json: bool = False


@dataclass(frozen=True)
class Graph:
    paper: Paper
    nodes: bool = False


@dataclass(frozen=True)
class History:
    paper: Paper
    node: str


@dataclass(frozen=True, kw_only=True)
class Query:
    """A bounded read over a kept state: its sections bounded by `limit`
    (0: all), each block's text cut short unless `whole`, JSON or lines."""

    paper: Paper
    limit: int = queries.LIMIT
    whole: bool = False
    json: bool = False


@dataclass(frozen=True, kw_only=True)
class Show(Query):
    block: str
    kind: tuple[str, ...] = ()
    history: bool = False


@dataclass(frozen=True, kw_only=True)
class Reach(Query):
    block: str
    through_claims: bool = False


@dataclass(frozen=True, kw_only=True)
class Blocks(Query):
    standing: tuple[str, ...] = ()
    role: tuple[str, ...] = ()
    form: tuple[str, ...] = ()
    section: tuple[str, ...] = ()
    group: tuple[str, ...] = ()
    onto: tuple[str, ...] = ()
    reached_from: tuple[str, ...] = ()
    kind: tuple[str, ...] = ()
    text: str | None = None
    changed_since: str | None = None
    uses_unsupported: bool = False
    judgment: tuple[str, ...] = ()
    aspect: tuple[str, ...] = ()
    open_findings: bool = False
    established: str | None = None
    provisional: bool = False


@dataclass(frozen=True, kw_only=True)
class Changes(Query):
    """Between two kept states, or the latest and the paper's files; its
    paper names no state."""

    older: str | None = None
    newer: str | None = None


@dataclass(frozen=True, kw_only=True)
class Reask(Query):
    judgment: str


@dataclass(frozen=True, kw_only=True)
class Open(Query):
    kind: tuple[str, ...] = ()
    section: tuple[str, ...] = ()
    by: str | None = None


@dataclass(frozen=True, kw_only=True)
class Inbox(Query):
    pass


@dataclass(frozen=True, kw_only=True)
class Check(Query):
    by: str | None = None


@dataclass(frozen=True)
class Settings:
    directory: Path
    json: bool = False


#: Each read over the kept states, by its command.
QUERIES: dict[str, type[Query]] = {
    "show": Show,
    "reach": Reach,
    "blocks": Blocks,
    "changes": Changes,
    "reask": Reask,
    "open": Open,
    "inbox": Inbox,
}


def request(arguments: argparse.Namespace) -> Any:
    """The read a command's parsed arguments ask."""
    command = arguments.command
    if command in ("read", "cut"):
        return requested(Document, arguments)
    if command == "compare":
        return requested(Compare, arguments)
    if command == "settings":
        return requested(Settings, arguments)
    kinds: dict[str, type[Any]] = {
        "states": States,
        "graph": Graph,
        "history": History,
        "check": Check,
        **QUERIES,
    }
    return requested(kinds[command], arguments, paper=paper_of(arguments))


def document(found: Document) -> str:
    """`read`: the paper's document layer; `cut`: with its cut."""
    read = read_paper(found.directory, found.root)
    if found.displays is None:
        return dumps(document_payload(read))
    return dumps(cut_payload(read, cut(read, CutRules(DisplayRule(found.displays)))))


def compare(found: Compare) -> str:
    rules = CutRules(DisplayRule(found.displays))
    sides = []
    for directory in (found.first, found.second):
        document = read_paper(directory)
        sides.append((document, cut(document, rules)))
    paired = correspond(sides[0], sides[1])
    if found.json:
        pairs = [
            {"kind": pair.kind.value, "a": list(pair.a), "b": list(pair.b)}
            for pair in paired.pairs
        ]
        return json.dumps(pairs, indent=2, ensure_ascii=False) + "\n"
    width = max(len(name) for name in paired.summary())
    return "".join(
        f"{name:<{width}}  {count}\n" for name, count in paired.summary().items()
    )


def states(found: States) -> str:
    """Each kept state, a line each: `*` on a head, its id, parent,
    command, version, time and nodes; then the person answers given on the
    latest's version that no state of it applied yet; then a line while
    the paper's files are not the latest state's version. `--json`: the
    same, as `states_and_files` gives it."""
    store = kept_store(found.paper)
    data = states_and_files(store, found.paper.directory)
    if found.json:
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    lines = [
        f"{'*' if s['head'] else ' '} {s['id']}  parent {s['parent'] or '-':<16}  "
        f"{s['command']:<6}  version {s['version'][:12]}  {s['at']}  "
        f"{s['nodes']} nodes"
        for s in data["states"]
    ]
    if data["pending"]:
        lines.append(
            "pending on the latest version: "
            + ", ".join(f"{a['key'][:12]} ({a['stage']})" for a in data["pending"])
        )
    if data["files"]["pending"]:
        lines.append(
            f"the paper's files (version {data['files']['version'][:12]}) are not "
            "the latest state's version: an edit not yet updated "
            "(`axiomation changes` shows it)"
        )
    return "\n".join(lines) + "\n"


def states_and_files(store: RecordStore, directory: Path) -> dict[str, Any]:
    """`states --json`: the kept states (`queries.states_data`) and the
    paper's files against the latest of them (`files_data`)."""
    data = queries.states_data(store)
    return data | {"files": files_data(store, directory, versions.latest_if_one(store))}


def files_data(
    store: RecordStore, directory: Path, latest: State | None
) -> dict[str, Any]:
    """The paper's files' version, read from the root file the latest state
    read (None where they, or the kept files they are compared with, cannot
    be read, or no state is kept), and whether
    it differs from that state's: an edit not yet updated."""
    if latest is None:
        return {"version": None, "state": None, "pending": False}
    version = files_version(store, directory, latest)
    return {
        "version": version,
        "state": latest.id,
        "pending": version is not None and version != latest.version,
    }


def graph(found: Graph) -> bytes:
    """A kept state's export as its run wrote it; with `nodes`, its node
    id per block and group node id per group instead."""
    store = kept_store(found.paper)
    kept = chosen(found.paper, store)
    if found.nodes:
        data = versions.graph(store, kept)
        del data["export"]
        return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return versions.kept_export(store, kept)


def history(found: History) -> str:
    store = kept_store(found.paper)
    kept = chosen(found.paper, store)
    steps = queries.history_data(store, found.node, kept)
    return json.dumps(steps, indent=2, ensure_ascii=False) + "\n"


def query(found: Query) -> str:
    """`show`, `reach`, `blocks`, `changes`, `reask`, `open` or `inbox`,
    rendered."""
    report = query_report(found, kept_store(found.paper), queries.View)
    if isinstance(found, Open) and found.by and not found.json:
        report = queries.grouped(report, queries.GroupBy(found.by))
    return queries.render(report, found.limit, found.json)


def query_report(found: Query, store: RecordStore, view_of: ViewOf) -> queries.Report:
    """A read's report, its states read by `view_of`: the command line's and
    the viewer's server's one path."""
    if isinstance(found, Changes):
        return _changes(found, store, view_of)
    kept = chosen(found.paper, store)
    view = view_of(store, kept)
    if isinstance(found, Show):
        named = queries.named(view, found.block)
        if not isinstance(named, str):
            if found.kind:
                raise UsageError(
                    f"{found.block!r} names a finding or a judgment, which has "
                    "no edges: --kind takes a block"
                )
            report = queries.filing_report(view, named, found.whole, found.history)
        else:
            steps = (
                versions.history(store, view.node(named), kept)
                if found.history and named in view.blocks
                else None
            )
            report = queries.show(view, named, list(found.kind), found.whole, steps)
    elif isinstance(found, Reach):
        report = queries.reach_report(view, found.block, found.through_claims)
    elif isinstance(found, Reask):
        report = queries.reask_report(view, found.judgment)
    elif isinstance(found, Open):
        gated = (
            checks.unsettled(view)
            if not found.kind or any(k.value in found.kind for k in queries.GATE_KINDS)
            else None
        )
        report = queries.open_report(view, list(found.kind), list(found.section), gated)
    elif isinstance(found, Inbox):
        report = inbox.inbox(view)
    else:
        assert isinstance(found, Blocks)
        report = queries.blocks_report(view, _filter(found, store, kept))
    report.notes += notes(found.paper, store, kept)
    return report


def check(found: Check) -> tuple[str, Exit]:
    """`check`: the gates at a kept state under the paper's `[gates]`,
    rendered; FAILED when a gate fails."""
    store = kept_store(found.paper)
    view = queries.View(store, chosen(found.paper, store))
    verdict = checks.verdict(view, found.paper.directory)
    if found.json:
        text = json.dumps(verdict.data(), indent=2, ensure_ascii=False) + "\n"
    else:
        report = checks.report(verdict)
        if found.by:
            report = queries.grouped(report, queries.GroupBy(found.by))
        report.notes += record_notes(store)
        text = queries.render(report, found.limit)
    return text, Exit.OK if verdict.passes else Exit.FAILED


def settings(found: Settings) -> str:
    """`settings`: every value in force for a paper under the sections the
    core reads, each with where it came from, as `sources status` prints
    its own; a section refused as its reader refuses it."""
    paper = found.directory
    if not paper.is_dir():
        raise UsageError(f"{paper} is no directory: name the paper's")
    read = load_settings(paper)
    modes = checks.modes(paper)
    found_computations = computations.load(paper)
    sections: dict[str, dict[str, dict[str, Any]]] = {}

    def put(section: str, key: str, value: Any, came: Any) -> None:
        sections.setdefault(section, {})[key] = {"value": value, "from": str(came)}

    for gate, mode in modes.items():
        put("gates", gate.value, mode.value, origin(read, "gates", gate.value))
    own, shared = read.tables(computations.SECTION)
    put(
        computations.SECTION,
        "directories",
        list({**shared, **own}.get("directories", computations.DEFAULT_DIRECTORIES)),
        origin(read, computations.SECTION, "directories"),
    )
    put(
        computations.SECTION,
        "run_timeout_seconds",
        found_computations.run_timeout_seconds,
        origin(read, computations.SECTION, "run_timeout_seconds"),
    )
    for suffix, command in found_computations.runners.items():
        put(
            computations.SECTION,
            f"runners.{suffix}",
            command,
            origin(read, computations.SECTION, "runners", suffix),
        )
    if "py" not in found_computations.runners:
        found_runner = computations.python_runner(found_computations)
        chose = found_runner.chose
        put(
            computations.SECTION,
            "runners.py",
            found_runner.command,
            "default"
            if chose is None
            else "found " + os.path.relpath(chose, read.paper),
        )
    cap, cap_from = run_dollars_max(read)
    put(BUDGET_SECTION, RUN_DOLLARS_MAX, cap, cap_from)
    bib, bib_from = bibliography_from(read)
    put("sources", "bib", None if bib is None else str(bib), bib_from)
    own, shared = read.tables("sources")
    for key in dict.fromkeys([*shared, *own]):
        if key != "bib":
            put("sources", key, {**shared, **own}[key], origin(read, "sources", key))
    person = user_settings.load()
    for user_key in UserKey:
        held = person.get(user_key)
        shown: str | None = held.value or user_settings.DEFAULTS.get(user_key)
        if user_key.secret:
            shown = "set" if held else "unset"
        put("user", user_key.value, shown, held.origin)
    ignored = [str(f) for f in ignored_env_files(read.paper, read.workspace_root)]
    files = [
        str(f)
        for f in (
            None if read.workspace_root is None else read.file(True),
            paper.resolve() / CONFIG_NAME,
        )
        if f is not None and f.is_file()
    ]
    if found.json:
        return (
            json.dumps(
                {
                    "paper": str(read.paper),
                    "workspace": None
                    if read.workspace_root is None
                    else str(read.workspace_root),
                    "files": files,
                    "user_file": str(person.file),
                    "ignored": ignored,
                    "sections": sections,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
    width = max(len(k) for table in sections.values() for k in table)
    lines = [
        f"paper      {read.paper}",
        "workspace  "
        + ("none" if read.workspace_root is None else str(read.workspace_root)),
        "files      " + (", ".join(files) or "none: every value is the default"),
        "user file  "
        + str(person.file)
        + ("" if person.file.is_file() else " (none: the shell and the defaults)"),
        *(f"ignored    {f}: axiomation reads no .env" for f in ignored),
    ]
    for name, table in sections.items():
        lines.append(f"[{name}]")
        for key, held in table.items():
            value = held["value"]
            shown = (
                "none"
                if value is None
                else ", ".join(map(str, value))
                if isinstance(value, list)
                else str(value)
            )
            lines.append(f"  {key:<{width}}  {shown}  ({held['from']})")
    lines.append(
        "note: `axiomation sources status PAPER` resolves the rest of [sources] "
        "(the collection, the model) and asks the services whether they answer"
    )
    return "\n".join(lines) + "\n"


def _filter(found: Blocks, store: RecordStore, kept: State) -> queries.Filter:
    since = found.changed_since
    if found.aspect and not found.judgment:
        raise UsageError("--aspect narrows --judgment: give the status too")
    return queries.Filter(
        standings=found.standing,
        roles=found.role,
        forms=found.form,
        sections=found.section,
        groups=found.group,
        onto=found.onto,
        reached_from=found.reached_from,
        kinds=found.kind,
        text=found.text,
        changed=None
        if since is None
        else queries.changed_since(versions.state(store, since), kept),
        uses_unsupported=found.uses_unsupported,
        judged=found.judgment,
        aspects=found.aspect,
        open_findings=found.open_findings,
        established=None if found.established is None else found.established == "yes",
        provisional=found.provisional,
    )


def notes(paper: Paper, store: RecordStore, kept: State) -> list[str]:
    """The notes under a read of `paper`: what the state shown does not
    hold: an edit, a person's or an agent's, no state applied yet (what it
    reopens not yet answered), and, for the latest state, an edit
    of the paper's files not yet updated; the person's filings that wait
    for their word."""
    found = queries.pending_notes(store, kept) + record_notes(store)
    if paper.state is None:
        version = files_version(store, paper.directory, kept)
        if version is not None and version != kept.version:
            found.append(
                "the paper's files are not this state's version: an edit not yet "
                "updated (`axiomation changes` shows it)"
            )
    return found


def record_notes(store: RecordStore) -> list[str]:
    """A note where the person's filings wait for their word (no read
    holds them until they confirm), and where this machine held no tip of
    the record before this read (a record cut short is caught only from
    now on)."""
    found = []
    if store.unpinned():
        found.append(
            "this machine held no tip of this record before: a record cut "
            "short or set back is caught from now on, not before"
        )
    waiting = len(store.waiting())
    if waiting:
        found.append(
            f"{waiting} of your filings wait for your word, without effect "
            "until you confirm them: `axiomation confirm`"
        )
    return found + checks.made_apart(store)


def files_version(store: RecordStore, directory: Path, kept: State) -> str | None:
    """The paper's files' version, read from the root file `kept` read:
    `kept`'s where today's reader reads them as its kept files
    (`versions.as_kept`), so a reader change alone is no edit; None where
    they, or the kept files they are compared with, cannot be read. Worked
    out once a process for the files the include walk reads, by path and
    content: a layer is read from them alone, so the same files give the
    same version."""
    directory = Path(directory)
    try:
        walked = walk(directory, find_root(directory, Path(kept.root)))
    except (ReaderError, OSError):
        return None
    key = (
        directory.resolve(),
        store.key,
        kept.id,
        tuple((source.path, source.sha256) for source in walked.files),
    )
    held = _FILES_VERSIONS.get(key, ())
    if held is not None:
        return held[0]
    try:
        found = document_hash(versions.as_kept(store, kept, read_layer(walked)))
    except (ReaderError, OSError, VersionError):
        found = None
    _FILES_VERSIONS.put(key, (), (found,), 1)
    return found


#: The paper's files' versions worked out, by the paper, the store, the
#: state compared with and the files read: the viewer asks every few
#: seconds whether the files are ahead of the latest state.
FILES_VERSIONS_HELD = 64
_FILES_VERSIONS: versions.Held[tuple[str | None]] = versions.Held(FILES_VERSIONS_HELD)


def _changes(read: Changes, store: RecordStore, view_of: ViewOf) -> queries.Report:
    """Between two kept states (`--from`, `--to`), or, with no `--to`
    where the paper's files are not the latest state's version, between
    a kept state and the files: an edit not yet updated."""
    newer = None if read.newer is None else versions.state(store, read.newer)
    if newer is None:
        latest = versions.latest(store)
        assert latest is not None
        version = files_version(store, read.paper.directory, latest)
        if version is not None and version != latest.version:
            older = latest if read.older is None else versions.state(store, read.older)
            return _pending(read, store, older)
        newer = latest
    if read.older is not None:
        older = versions.state(store, read.older)
    elif newer.parent is not None:
        older = versions.state(store, newer.parent)
    else:
        raise UsageError(f"state {newer.id} has no parent: name one with --from")
    return queries.changes_report(
        store, view_of(store, older), view_of(store, newer), read.whole
    )


def _pending(read: Changes, store: RecordStore, older: State) -> queries.Report:
    """The paper's files against a kept state: the identity answer and the
    update's plan `update --dry-run` would make from it, in memory, nothing
    asked or recorded. The paper is read with its vocabulary map, the kept
    state's carried and the leftovers where the store answered them, as the
    update would read it."""
    directory = read.paper.directory
    document = read_paper(directory, Path(older.root))
    found_map = map_vocabulary(
        document,
        store,
        Templates.shipped(),
        kept=vocabulary_of(versions.kept_payload(store, older)),
    )
    if found_map.vocabulary is not None and found_map.vocabulary != Vocabulary():
        document = read_paper(directory, Path(older.root), found_map.vocabulary)
    files = tuple(sheet_files(directory, document.never_included))
    # an export names its rules; `update`'s default where one does not
    rules = CutRules(DisplayRule.IN_PARAGRAPH)
    with tempfile.TemporaryDirectory(prefix="axiomation-old-") as old_directory:
        old_document = versions.materialize(store, older, Path(old_directory))
        exported = versions.kept_payload(store, older)
        old_cut = cut_from_payload(old_document, exported, rules, roles=True)
        try:
            made = made_identity(
                old_document, old_cut, exported, document, files, store, older
            )
        except UsageError as error:
            raise UsageError(
                f"state {older.id} cannot be updated from, so its changes cannot be "
                f"read: {error}"
            ) from None
    made_plan = planned(
        document,
        store,
        UpdateSettings(files=files, limit=None),
        made.key,
        made.payload,
        made.links,
    )
    by_handle = made_plan.handles
    return queries.pending_report(
        queries.View(store, older),
        made.payload,
        {
            block.id: es.norm(" ".join(document.text(run) for run in block.owned))
            for block in made_plan.cut.blocks
        },
        {ident: handle for handle, ident in by_handle.items()},
        {h: (r.why, r.origin) for h, r in made_plan.reopened.items()},
        made_plan.conditional,
        document_hash(document),
        read.whole,
    )


def parser() -> argparse.ArgumentParser:
    """The reads over the kept states alone, as the command line parses
    them: the viewer's server reads its requests' parameters by their flags."""
    found = argparse.ArgumentParser(prog="axiomation")
    commands = found.add_subparsers(dest="command", required=True)
    for command in QUERIES:
        ARGUMENTS[command](commands)
    return found


def _read_arguments(commands: Any) -> None:
    read = commands.add_parser(
        "read", help="write a paper's document layer in the exchange format"
    )
    paper_arguments(read)
    output_argument(read)


def _cut_arguments(commands: Any) -> None:
    cutting = commands.add_parser(
        "cut", help="write a paper's document layer and its cut"
    )
    paper_arguments(cutting)
    displays_argument(cutting)
    output_argument(cutting)


def _compare_arguments(commands: Any) -> None:
    compare = commands.add_parser(
        "compare", help="cut two papers and line their blocks up"
    )
    compare.add_argument("first", type=Path, help="the first paper's directory")
    compare.add_argument("second", type=Path, help="the second paper's directory")
    displays_argument(compare)
    compare.add_argument(
        "--json", action="store_true", help="print the pairs, not the summary"
    )


def _states_arguments(commands: Any) -> None:
    states = commands.add_parser(
        "states", help="the states a paper's store keeps, oldest first"
    )
    kept_arguments(states)
    states.add_argument("--json", action="store_true", help="JSON, not lines")


def _graph_arguments(commands: Any) -> None:
    graph = commands.add_parser(
        "graph", help="a kept state's export, as its run wrote it"
    )
    kept_arguments(graph)
    graph.add_argument(
        "--nodes",
        action="store_true",
        help="the state's node id per block and group node id per group instead",
    )
    output_argument(graph)


def _history_arguments(commands: Any) -> None:
    history = commands.add_parser(
        "history",
        help="a node's history across the kept states: each step's change, "
        "causes and text",
    )
    kept_arguments(history)
    history.add_argument(
        "node",
        help="a node id, a handle, a block's id at the state, or a finding's or "
        "a judgment's id (its block's node) or a unique prefix of it",
    )


def _show_arguments(commands: Any) -> None:
    show = commands.add_parser(
        "show",
        help="a block at a kept state: its line, text, what it rests on and what "
        "rests on it, each in the paper's order, its standings; or a finding or "
        "a judgment by its id: the block it is on, its anchors, answers and "
        "earlier filings, or its causes and its subject's status",
    )
    _query_arguments(show)
    show.add_argument(
        "block",
        help="a node id, a handle at the state, a block or group id, or a "
        "handle's unique prefix; else a finding's or a judgment's id or a "
        "unique prefix of it",
    )
    show.add_argument(
        "--kind",
        action="append",
        default=[],
        choices=[*KINDS, "self"],
        help="its edges of this kind only (repeat for several)",
    )
    show.add_argument("--history", action="store_true", help="add the node's history")


def _reach_arguments(commands: Any) -> None:
    reach_ = commands.add_parser(
        "reach",
        help="the blocks an edit to a block may reach at a kept state, at most "
        "(the reach rule: docs/model.md § 5)",
    )
    _query_arguments(reach_)
    _block_reference(reach_)
    reach_.add_argument(
        "--through-claims",
        action="store_true",
        help="walk on past each asked block as though its answer were that it "
        "changed: the most the edit can reach, each block beyond naming the asked "
        "block it came past",
    )


def _blocks_arguments(commands: Any) -> None:
    blocks = commands.add_parser(
        "blocks",
        help="the blocks of a kept state that pass every filter given, a count "
        "first, then a line each",
        # a flag's prefix is no flag: `--uses`, gone, is not `--uses-unsupported`
        allow_abbrev=False,
    )
    _query_arguments(blocks)
    for flag, what, extra in (
        ("--standing", "holding a standing of this status", {"choices": STATUSES}),
        ("--role", "with this role", {"choices": [*ROLES, "none"]}),
        ("--form", "of this form or environment (prose, display, theorem, …)", {}),
        (
            "--section",
            "in this section: its number (2 takes 2.1 too) or its title words (that "
            "section alone)",
            {},
        ),
        ("--group", "in this group", {}),
        (
            "--onto",
            "with an edge onto this block or group (not onto its group): with "
            "--kind uses, its users; justifies, its proofs; qualifies, what "
            "qualifies it",
            {"metavar": "BLOCK"},
        ),
        (
            "--from",
            "that this block's edges reach (a group: its blocks): with --kind "
            "uses, what it uses; justifies, what it proves; qualifies, what it "
            "qualifies",
            {"dest": "reached_from", "metavar": "BLOCK"},
        ),
    ):
        blocks.add_argument(
            flag, action="append", default=[], help=f"{what} (repeat: any)", **extra
        )
    blocks.add_argument(
        "--kind",
        action="append",
        default=[],
        choices=KINDS,
        help="--onto and --from count edges of this kind only",
    )
    blocks.add_argument("--text", help="whose text matches this regular expression")
    blocks.add_argument(
        "--changed-since",
        metavar="STATE",
        help="whose text changed since this kept state, or that it does not hold",
    )
    blocks.add_argument(
        "--uses-unsupported",
        action="store_true",
        help="with a uses edge onto a block holding an unsupported claim",
    )
    blocks.add_argument(
        "--judgment",
        action="append",
        default=[],
        choices=[k.value for k in judgments.StatusKind],
        help="with a subject of this status (repeat: any)",
    )
    blocks.add_argument(
        "--aspect",
        action="append",
        default=[],
        choices=[a.value for a in JUDGED_ASPECTS],
        help="--judgment counts subjects of this aspect only (repeat: any)",
    )
    blocks.add_argument(
        "--open-findings",
        action="store_true",
        help="with an open finding that has not lapsed",
    )
    blocks.add_argument(
        "--established",
        choices=("yes", "no"),
        help="with a claim established, or one not (a block holding several "
        "claims may answer both)",
    )
    blocks.add_argument(
        "--provisional",
        action="store_true",
        help="with a judgment in force that read a copy of a cited work that is "
        "not its version of record",
    )


def _changes_arguments(commands: Any) -> None:
    changes = commands.add_parser(
        "changes",
        help="what changed between two kept states, or between the latest and "
        "the paper's files: the text, then the graph, a line a split or a merge "
        "carried named once",
    )
    _query_arguments(changes, state=False)
    changes.add_argument(
        "--from",
        dest="older",
        metavar="STATE",
        help="the earlier state (default: --to's parent, or the latest against "
        "the paper's files)",
    )
    changes.add_argument(
        "--to",
        dest="newer",
        metavar="STATE",
        help="the later state (default: the paper's files where they are not the "
        "latest state's version, else the latest state)",
    )


def _open_arguments(commands: Any) -> None:
    open_ = commands.add_parser(
        "open",
        help="what is open at a kept state, a section per kind (--kind): "
        "unsupported claims, stale marks, open findings, lapsed judgments, …",
    )
    _query_arguments(open_)
    open_.add_argument(
        "--kind",
        action="append",
        default=[],
        choices=[k.value for k in queries.OpenKind],
        metavar="KIND",
        help="this kind only (repeat for several); the kinds: "
        + ", ".join(k.value for k in queries.OpenKind)
        + " (each: docs/model.md § 10)",
    )
    open_.add_argument(
        "--section",
        action="append",
        default=[],
        help="in this section: its number (2 takes 2.1 too) or its title words "
        "(that section alone; repeat: any)",
    )
    _by_argument(open_, "kind's")


def _inbox_arguments(commands: Any) -> None:
    inbox_ = commands.add_parser(
        "inbox",
        help="the decisions only you can make at a kept state, a section each, "
        "with each one's next step",
    )
    _query_arguments(inbox_)


def _check_arguments(commands: Any) -> None:
    check_ = commands.add_parser(
        "check",
        help="whether the paper passes at a kept state: each gate and the items "
        "in its way; exit 1 when a gate fails",
    )
    _query_arguments(check_)
    _by_argument(check_, "gate's")


def _settings_arguments(commands: Any) -> None:
    settings_ = commands.add_parser(
        "settings",
        help="the settings in force for a paper, each with where it came from, "
        "and yours (a key shown set or unset)",
    )
    directory_argument(settings_)
    settings_.add_argument(
        "--json",
        action="store_true",
        help=JSON_HELP,
    )


def _reask_arguments(commands: Any) -> None:
    reask = commands.add_parser(
        "reask",
        help="a judgment's re-ask brief at a kept state: every cause that lapsed "
        "it, then its subject and basis as they read now",
    )
    _query_arguments(reask)
    reask.add_argument("judgment", help="a judgment's id, or a unique prefix of it")


#: Each read's arguments, by its command.
ARGUMENTS: dict[str, Callable[[Any], None]] = {
    "read": _read_arguments,
    "cut": _cut_arguments,
    "compare": _compare_arguments,
    "states": _states_arguments,
    "graph": _graph_arguments,
    "history": _history_arguments,
    "show": _show_arguments,
    "reach": _reach_arguments,
    "blocks": _blocks_arguments,
    "changes": _changes_arguments,
    "open": _open_arguments,
    "inbox": _inbox_arguments,
    "check": _check_arguments,
    "settings": _settings_arguments,
    "reask": _reask_arguments,
}


#: A read's `--json` help: its shape is the guide's.
JSON_HELP = "JSON, not lines (its shape: docs/guide.md § 3)"


def _query_arguments(command: argparse.ArgumentParser, state: bool = True) -> None:
    """A read's arguments: the kept state's, the bound and JSON."""
    if state:
        kept_arguments(command)
    else:
        directory_argument(command)
        command.add_argument(
            "--store",
            type=Path,
            help=STORE_HELP,
        )
    command.add_argument(
        "--limit",
        type=int,
        default=queries.LIMIT,
        help=f"items a section shows (default {queries.LIMIT}; 0: all)",
    )
    command.add_argument(
        "--whole", action="store_true", help="each block's text whole, not cut short"
    )
    command.add_argument("--json", action="store_true", help=JSON_HELP)


def _by_argument(command: argparse.ArgumentParser, whose: str) -> None:
    command.add_argument(
        "--by",
        choices=[g.value for g in queries.GroupBy],
        help=f"each {whose} items grouped in the text: by the outermost section "
        "that holds them, their block's roles, or a claim's `true` reading (the "
        "JSON's items carry these fields: top_section, roles, reading)",
    )


def _block_reference(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "block",
        help="a node id, a handle at the state, a block or group id, or a "
        "handle's unique prefix",
    )
