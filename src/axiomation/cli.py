"""The `axiomation` command.

- `axiomation read DIR`: the document layer, in the exchange format;
- `axiomation cut DIR`: the document layer and the cut;
- `axiomation compare DIR_A DIR_B`: two papers cut and lined up;
- `axiomation diff OLD NEW`: two versions of a paper, each block of OLD
  paired with NEW's and classed;
- `axiomation review-cut DIR`: the cut reviewed by a model,
  a session per chunk, its answers recorded, the corrected cut exported;
  the roles, a session per chunk on roles.md;
  on Claude Code or OpenCode, the judge on a host of its own;
  under a profile, each stage on its own;
- `axiomation build DIR`: the whole graph, `review-cut` with
  every stage in order, the cuts, the roles, the groups and the edges;
- `axiomation update NEW`: the graph taken onto a new version,
  asking only what the change touched, from the store's identity answer
  for NEW, or one `--from OLD --cut CUT` makes; from the cutter,
  or with `--hold-cut` on the rebased cut's boundaries; `--from-nothing`
  is the update from no version, `build`'s run;
- `axiomation sheet DIR --stage X -o FILE`: a stage's file as a kept
  state shows it, for a person or an agent to edit; `axiomation edit DIR
  --stage X --file FILE`: the edited file checked by its stage's own check
  and recorded as an answer given on the state's version, an edges edit a
  new state at once; `axiomation change DIR [--add|--retract|--keep|
  --standing OWNER LINE] [--fragment FILE]`: a few edges lines, the same
  way; nothing replayed;
- `axiomation states DIR`, `graph DIR [--state S]` and `history DIR NODE`:
  the states the paper's store keeps, a kept state's export as its run
  wrote it, and a node's history across them;
- `axiomation show DIR BLOCK`, `reach DIR BLOCK`, `blocks DIR [filters]`
  and `changes DIR [--from S] [--to S]`: a block with its edges, what an
  edit to it reaches, blocks by filter, and what changed between two kept
  states or since the latest, bounded lines an agent greps;
- `axiomation judge DIR`, `find DIR` and `answer-finding DIR FINDING`: a
  judgment, a finding or a finding's answer filed on a kept state by a
  person or an agent, minting no state, a cited one naming its work
  (`--work`) and the pages it read (`--page KEY:INDEX`, read again and
  stamped); `reask DIR JUDGMENT`: a lapsed judgment's re-ask
  brief; `reaffirm DIR JUDGMENT` and `reaffirm-finding DIR FINDING`: a
  lapsed judgment or finding found standing, filed again on the state;
- `axiomation open DIR`: what is open at a kept state, a section per kind;
  `circles DIR [--ask]`: its circles of what claims rest on, and an
  agent's question naming their lines' ends; `support DIR [--ask]`: its
  claims standing unsupported, and an agent's search for their support;
  `view DIR`: the viewer served on 127.0.0.1, read-only.

`review-cut`, `build`, `update`, `sheet`, `edit`, `change` and the reads
take the paper's own store, `DIR/axiomation/`, where `--store` names
none; a whole `build` or `update` keeps its state there, and `update DIR`
with neither `--from` nor `--identity` updates from the store's latest
state alone.
"""

from __future__ import annotations

import argparse
import atexit
import gc
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from axiomation import (
    __version__,
    circles,
    develop,
    edits,
    evidence,
    judgments,
    queries,
    rereads,
    review,
    support_search,
    versions,
)
from axiomation.commands import (
    Exit,
    Say,
    UsageError,
    asks,
    build,
    chosen,
    filings,
    kept_arguments,
    kept_store,
    paper_of,
    reads,
    sync,
)
from axiomation.commands import edits as editing
from axiomation.commands import init as initing
from axiomation.commands import keys as keying
from axiomation.commands import review as reviewing
from axiomation.commands import shares as sharing
from axiomation.commands import update as update_command
from axiomation.exchange import (
    ExchangeError,
)

# the build's stages, as callers of the command import them
from axiomation.paper_argument import PaperNotFound, with_paper
from axiomation.profiles import BUILD_STAGES as BUILD_STAGES
from axiomation.questions.cut_file import RenderError
from axiomation.reader import (
    ReaderError,
)
from axiomation.records import (
    RecordError,
)
from axiomation.resolver import StoreBusy
from axiomation.runner.opencode import HostSetupError
from axiomation.settings import (
    SettingsError,
)
from axiomation.update import (
    UpdateError,
)
from axiomation.versions import VersionError
from axiomation.viewer import server as viewer_server
from axiomation.viewer.overlays import OverlayError, read_overlay

#: The commands whose `--store` defaults to the paper's own.
KEPT_COMMANDS = (
    "review-cut",
    "build",
    "update",
    "sync",
    "sheet",
    "edit",
    "change",
    "states",
    "graph",
    "history",
    "show",
    "reach",
    "blocks",
    "changes",
    "open",
    "inbox",
    "check",
    "view",
    "judge",
    "find",
    "answer-finding",
    "suggest",
    "answer-suggestion",
    "reaffirm",
    "reaffirm-finding",
    "reask",
    "review",
    "adopt",
    "rule",
    "check-contribution",
    "reread",
    "circles",
    "support",
    "run",
    "confirm",
    "members",
    "join",
    "share",
    "receive",
)


#: The commands the top-level help leaves out, the shares: each is added
#: with a description and no help, and answers its own `--help`.
UNLISTED_COMMANDS = ("share", "journal", "review-share", "receive")

#: The commands whose arguments after `--` are a script's.
RUNNING = ("judge", "run")


class _PrintVersion(argparse.Action):
    """`--version` as one line: argparse's own version action wraps its text
    at the terminal's width, which breaks a long path in two."""

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        # The checkout's path beside the number tells an editable install of
        # this package from another package installed under the same command.
        package = Path(__file__).resolve().parent
        print(f"axiomation {__version__} ({package})")
        parser.exit()


def parser() -> argparse.ArgumentParser:
    found = argparse.ArgumentParser(
        prog="axiomation", description="Build the argument graph of a paper."
    )
    found.add_argument(
        "--version",
        nargs=0,
        action=_PrintVersion,
        help="show the version and the package's path, and exit",
    )
    commands = found.add_subparsers(dest="command", required=True, metavar="COMMAND")
    # listed by use: the loop the README walks first, then the reads, the
    # edits and filings, the set-up, and the document layer's tools last
    build.ARGUMENTS["build"](commands)
    update_command.ARGUMENTS["update"](commands)
    sync.ARGUMENTS["sync"](commands)
    reads.ARGUMENTS["open"](commands)
    reviewing.ARGUMENTS["review"](commands)
    for command in ("check", "changes", "show", "inbox"):
        reads.ARGUMENTS[command](commands)
    view = commands.add_parser(
        "view",
        help="serve the viewer on 127.0.0.1 until Ctrl-C: a kept state's text, "
        "argument and what is open, read-only",
    )
    kept_arguments(view)
    view.add_argument(
        "--port", type=int, default=0, help="the port (default: a free one)"
    )
    view.add_argument(
        "--open", action="store_true", help="open the page in the browser"
    )
    view.add_argument(
        "--overlay",
        action="append",
        default=[],
        type=Path,
        metavar="FILE",
        help="an overlay's JSON file, its items drawn at their anchors (repeat "
        "for several)",
    )
    for command in ("states", "graph", "history", "reach", "blocks"):
        reads.ARGUMENTS[command](commands)
    for command in ("sheet", "edit", "change"):
        editing.ARGUMENTS[command](commands)
    for command in filings.ARGUMENTS:
        filings.ARGUMENTS[command](commands)
    for command in asks.ARGUMENTS:
        asks.ARGUMENTS[command](commands)
    reads.ARGUMENTS["reask"](commands)
    commands.add_parser(
        "sources",
        add_help=False,
        help="read the cited works' pages, each marked with where it came from, "
        "and build their corpus: `axiomation sources ACTION DIR …`",
    ).add_argument("rest", nargs=argparse.REMAINDER)
    initing.ARGUMENTS["init"](commands)
    reads.ARGUMENTS["settings"](commands)
    keying.ARGUMENTS["keys"](commands)
    keying.ARGUMENTS["confirm"](commands)
    keying.ARGUMENTS["members"](commands)
    keying.ARGUMENTS["join"](commands)
    for command in ("read", "cut", "compare"):
        reads.ARGUMENTS[command](commands)
    update_command.ARGUMENTS["diff"](commands)
    build.ARGUMENTS["review-cut"](commands)
    for command in UNLISTED_COMMANDS:
        sharing.ARGUMENTS[command](commands)
    return found


def _run_view(arguments: argparse.Namespace) -> int:
    """Serve the viewer until interrupted: its URL on stdout, the page on
    the state given (else the latest)."""
    paper = paper_of(arguments)
    store = kept_store(paper)
    kept = chosen(paper, store) if arguments.state is not None else None
    try:
        overlays = [read_overlay(path) for path in arguments.overlay]
    except OverlayError as error:
        raise UsageError(str(error)) from None
    server = viewer_server.serve(
        store, arguments.directory, arguments.port, overlays, warm=True
    )
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if kept is not None:
        url += f"#state={kept.id}"
    print(url, flush=True)
    if arguments.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _write(text: str | bytes, output: Path | None) -> None:
    data = text.encode("utf-8") if isinstance(text, str) else text
    if output is None:
        sys.stdout.buffer.write(data)
        sys.stdout.flush()
    else:
        output.write_bytes(data)


def _from_nothing(argv: Sequence[str]) -> list[str]:
    """`update NEW --from-nothing …` as the `build NEW …` it is, `build = update(∅ →
    v)`: the same arguments, build's options among them, with nothing to update from."""
    given = [a for a in argv[1:] if a != "--from-nothing"]
    refused = [
        flag
        for flag in ("--from", "--identity", "--hold-cut")
        if any(a == flag or a.startswith(f"{flag}=") for a in given)
    ]
    if refused:
        raise UsageError(
            f"--from-nothing updates from no version: {', '.join(refused)} names one"
        )
    return ["build", *given]


def _default_store(arguments: argparse.Namespace) -> None:
    """`--store` left out: the paper's own, `<paper>/axiomation/`;
    made with its `.gitignore` only once a run writes to it."""
    if arguments.command in KEPT_COMMANDS and (arguments.store is None):
        arguments.store = versions.store_of(arguments.directory)


def _run_sources(rest: list[str]) -> int:
    """`axiomation sources …`: the cited works' own command, one of the
    core's ways into that package."""
    from axiomation.sources.cli import main as sources_main

    return sources_main(rest)


#: The cyclic collector's first threshold in a command's own process: a read
#: builds millions of objects that live until it ends, and the default
#: (700) walks them again and again.
COLLECT_AFTER_ALLOCATIONS = 50_000


def _as_its_own_process() -> None:
    """The command runs as its own process (the `axiomation` script): the
    cyclic collector runs less often, and not as the process ends, when its
    memory goes back whole."""
    gc.set_threshold(COLLECT_AFTER_ALLOCATIONS, 50, 100)
    atexit.register(gc.disable)


def _read(arguments: argparse.Namespace) -> tuple[str | bytes, Path | None]:
    """A read's result, and the file it is written to (None: stdout)."""
    asked = reads.request(arguments)
    match asked:
        case reads.Document():
            return reads.document(asked), arguments.output
        case reads.Compare():
            return reads.compare(asked), None
        case reads.States():
            return reads.states(asked), None
        case reads.Graph():
            return reads.graph(asked), arguments.output
        case reads.History():
            return reads.history(asked), None
        case reads.Settings():
            return reads.settings(asked), None
    return reads.query(asked), None


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        _as_its_own_process()
    given = list(sys.argv[1:] if argv is None else argv)
    if given[:1] == ["sources"]:
        # its own parser reads the rest whole: a remainder argument would
        # refuse a leading option such as `-h`
        return _run_sources(given[1:])
    parsing = parser()
    try:
        found = with_paper(parsing, given)
    except PaperNotFound as error:
        print(f"axiomation {given[0]}: {error}", file=sys.stderr)
        return 1
    given = found.argv
    if found.guessed is not None:
        print(
            f"axiomation {given[0]}: note: no paper above here; {found.guessed} "
            "taken as the paper",
            file=sys.stderr,
        )
    if given[:1] == ["update"] and "--from-nothing" in given:
        # the build's run: same function, same export
        try:
            arguments = parsing.parse_args(_from_nothing(given))
            _default_store(arguments)
            return build.run(build.request(arguments), Say())
        except (
            ReaderError,
            OSError,
            UsageError,
            develop.DevelopError,
            StoreBusy,
            RecordError,
            HostSetupError,
            RenderError,
            ExchangeError,
            VersionError,
            SettingsError,
        ) as error:
            print(f"axiomation update: {error}", file=sys.stderr)
            return 1
    script: list[str] = []
    if given[:1] and given[0] in RUNNING and "--" in given:
        # a script's own arguments, after `--`, never parsed here
        at = given.index("--")
        given, script = given[:at], given[at + 1 :]
    arguments = parsing.parse_args(given)
    arguments.run_arguments = tuple(script)
    _default_store(arguments)
    if arguments.command == "sources":
        return _run_sources(arguments.rest)
    if arguments.command == "init":
        try:
            return initing.run(initing.request(arguments), Say())
        except (OSError, UsageError, SettingsError) as error:
            print(f"axiomation init: {error}", file=sys.stderr)
            return 1
    if arguments.command in ("keys", "confirm", "members", "join"):
        try:
            return keying.run(keying.request(arguments), Say())
        except (OSError, UsageError) as error:
            print(f"axiomation {arguments.command}: {error}", file=sys.stderr)
            return 1
    if arguments.command in ("share", "journal", "review-share", "receive"):
        try:
            return sharing.run(sharing.request(arguments), Say())
        except (OSError, UsageError) as error:
            print(f"axiomation {arguments.command}: {error}", file=sys.stderr)
            return 1
    if arguments.command in ("sheet", "edit", "change"):
        try:
            asked = editing.request(arguments)
            match asked:
                case editing.Sheet():
                    return editing.sheet(asked, Say())
                case editing.Edit():
                    return editing.edit(asked, Say())
            return editing.change(asked, Say())
        except (
            ReaderError,
            OSError,
            UsageError,
            develop.DevelopError,
            UpdateError,
            StoreBusy,
            RecordError,
            RenderError,
            ExchangeError,
            VersionError,
            edits.EditError,
            queries.QueryError,
        ) as error:
            print(f"axiomation {arguments.command}: {error}", file=sys.stderr)
            return 1
    if arguments.command == "review":
        try:
            return reviewing.run(reviewing.request(arguments), Say()).code
        except (
            ReaderError,
            OSError,
            UsageError,
            develop.DevelopError,
            StoreBusy,
            RecordError,
            HostSetupError,
            VersionError,
            edits.EditError,
            review.ReviewError,
            evidence.EvidenceError,
        ) as error:
            print(f"axiomation review: {error}", file=sys.stderr)
            return 1
    if arguments.command == "sync":
        try:
            return sync.run(sync.request(arguments), Say())
        except (
            ReaderError,
            OSError,
            UsageError,
            develop.DevelopError,
            UpdateError,
            StoreBusy,
            RecordError,
            HostSetupError,
            RenderError,
            ExchangeError,
            VersionError,
            SettingsError,
            edits.EditError,
            review.ReviewError,
            evidence.EvidenceError,
        ) as error:
            print(f"axiomation sync: {error}", file=sys.stderr)
            return 1
    if arguments.command == "update":
        try:
            asked = update_command.request(arguments)
            return update_command.run(asked, Say()).code
        except (
            ReaderError,
            OSError,
            UsageError,
            develop.DevelopError,
            UpdateError,
            StoreBusy,
            RecordError,
            HostSetupError,
            RenderError,
            ExchangeError,
            VersionError,
            SettingsError,
        ) as error:
            print(f"axiomation update: {error}", file=sys.stderr)
            return 1
    if arguments.command in ("review-cut", "build"):
        try:
            return build.run(build.request(arguments), Say())
        except (
            ReaderError,
            OSError,
            UsageError,
            develop.DevelopError,
            StoreBusy,
            RecordError,
            HostSetupError,
            RenderError,
            ExchangeError,
            VersionError,
            SettingsError,
        ) as error:
            print(f"axiomation {arguments.command}: {error}", file=sys.stderr)
            return 1
    try:
        if arguments.command == "diff":
            _write(update_command.diff(update_command.diff_request(arguments)), None)
            return 0
        if arguments.command == "view":
            return _run_view(arguments)
        if arguments.command == "check":
            text, code = reads.check(reads.request(arguments))
            _write(text, None)
            return code
        if arguments.command in reads.ARGUMENTS:
            _write(*_read(arguments))
            return 0
        if arguments.command in filings.KINDS:
            _write(filings.file(filings.request(arguments), Say()), None)
            return 0
        if arguments.command == "circles":
            _write(asks.circles_of(asks.request(arguments), Say()).text, None)
            return 0
        if arguments.command == "support":
            step = asks.support(asks.request(arguments), Say())
            _write(step.text, None)
            return Exit.BREACH if step.breached else 0
        raise AssertionError(f"no command {arguments.command}")
    except (
        ReaderError,
        OSError,
        UsageError,
        develop.DevelopError,
        RecordError,
        ExchangeError,
        StoreBusy,
        VersionError,
        UpdateError,
        queries.QueryError,
        judgments.JudgmentError,
        evidence.EvidenceError,
        SettingsError,
        edits.EditError,
        rereads.RereadError,
        circles.CircleError,
        support_search.SearchError,
    ) as error:
        print(f"axiomation {arguments.command}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
