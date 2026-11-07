"""The edits of a kept state by a person or an agent: `sheet`, a stage's
file as the state shows it; `edit`, that file edited and given; `change`,
a few edges lines given; each checked by its stage's check, a new state
once answered, and what it changed said."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from axiomation import check, edits, person, queries, versions
from axiomation.assembler import corrected_cut
from axiomation.changes import GraphState, Layer, graph_changes
from axiomation.commands import (
    Exit,
    Paper,
    Say,
    UsageError,
    chosen,
    kept_arguments,
    kept_on,
    kept_store,
    output_argument,
    paper_of,
    reads,
    requested,
    scratch_copy,
    seating,
    written,
)
from axiomation.commands import update as updating
from axiomation.commands.seating import Asking
from axiomation.driver import ReviewSettings
from axiomation.profiles import Agent, Profile, review_settings
from axiomation.questions import Question, Stage, edges_file
from axiomation.questions import edge_sheet as es
from axiomation.records import EdgeRecords
from axiomation.runner import Stop
from axiomation.runner.hosts import estimate

#: `sheet` and `edit`'s stage for the vocabulary map: no layer of the
#: graph, it is read before the cut.
VOCABULARY_STAGE = Stage.VOCABULARY.value


@dataclass(frozen=True, kw_only=True)
class Sheet:
    paper: Paper
    layer: str
    output: Path | None = None
    asking: Asking = field(default_factory=Asking)


@dataclass(frozen=True, kw_only=True)
class Given:
    """An edit given on a kept state: `edit`'s file, or `change`'s lines;
    who gives it and why, how its result prints, and, for `edit`, who
    answers what it reopens (`change` asks nothing: `asking` None)."""

    command: str
    paper: Paper
    agent: str | None = None
    session: str | None = None
    reason: str | None = None
    limit: int = queries.LIMIT
    json: bool = False
    dry_run: bool = False
    asking: Asking | None = None


@dataclass(frozen=True, kw_only=True)
class Edit(Given):
    layer: str
    file: Path


@dataclass(frozen=True, kw_only=True)
class Change(Given):
    #: the fragment's text, read from its file or stdin by `request`
    fragment: str | None = None
    add: tuple[tuple[str, str], ...] = ()
    retract: tuple[tuple[str, str], ...] = ()
    keep: tuple[tuple[str, str], ...] = ()
    standing: tuple[tuple[str, str], ...] = ()
    delta: tuple[tuple[str, str], ...] = ()
    part: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # refused before a fragment is read: `request` reads it after this
        if (self.retract or self.keep) and not self.reason:
            raise UsageError("a retract or a keep needs --reason")


def request(arguments: argparse.Namespace) -> Sheet | Edit | Change:
    """`sheet`'s, `edit`'s or `change`'s request from its parsed arguments."""
    paper = paper_of(arguments)
    if arguments.command == "sheet":
        return requested(
            Sheet, arguments, paper=paper, asking=seating.asking(arguments)
        )
    if arguments.command == "edit":
        return requested(Edit, arguments, paper=paper, asking=seating.asking(arguments))
    asked = requested(Change, arguments, paper=paper, fragment=None)
    if arguments.fragment is None:
        return asked
    # a command reads no stdin: a front end over stdio owns it
    fragment = (
        sys.stdin.read()
        if str(arguments.fragment) == "-"
        else Path(arguments.fragment).read_text(encoding="utf-8")
    )
    return replace(asked, fragment=fragment)


def sheet(asked: Sheet, say: Say) -> int:
    """`sheet PAPER --stage X [--state S] [-o FILE]`: the stage's
    file, whole paper, as the kept state shows it, for a person or an agent
    to edit; nothing replayed."""
    store = kept_store(asked.paper)
    kept = chosen(asked.paper, store, waiting=True)
    settings = review_settings(seating.profile_of(asked.asking))
    with edits.opened(store, kept, settings) as opened:
        if asked.layer == VOCABULARY_STAGE:
            text = edits.vocabulary_shown(opened)
        else:
            text = edits.shown(opened, Layer(asked.layer))
            _kept_notes("sheet", opened, Layer(asked.layer), say)
    written(text, asked.output, say)
    for note in reads.notes(asked.paper, store, kept):
        say.note(f"axiomation sheet: note: {note}")
    return 0


#: How many of a state's own problems a command names.
KEPT_SHOWN = 5


def _kept_notes(command: str, opened: edits.Kept, layer: Layer, say: Say) -> None:
    """What today's check refuses in the stage's file as the kept state
    shows it, named beside the report: the state's, which no edit is
    refused for."""
    found = edits.kept_problems(opened, layer)
    if not found:
        return
    listed = "".join(f"\n- {p}" for p in found[:KEPT_SHOWN])
    more = f"\n- and {len(found) - KEPT_SHOWN} more" if len(found) > KEPT_SHOWN else ""
    say.note(
        f"axiomation {command}: note: state {opened.state.id} holds "
        f"{len(found)} problem(s) today's check finds in its {layer.value} file; "
        f"they are the state's, and no edit is refused for them:{listed}{more}"
    )


def edit(asked: Edit, say: Say) -> int:
    """`edit PAPER --stage X --file F`: the edited file given on
    the kept state (`_given`)."""
    edited = asked.file.read_text(encoding="utf-8")
    if asked.layer == VOCABULARY_STAGE:
        return _vocabulary_edit(asked, edited, say)
    return _given(asked, Layer(asked.layer), lambda _kept, _shown: edited, say)


def _vocabulary_edit(asked: Edit, edited: str, say: Say) -> int:
    """`edit PAPER --stage vocabulary --file F`: the whole map given on the
    kept state (`edits.give_vocabulary`), then applied by the update of its
    version onto itself: the kept files read with the map given, every
    block that reads a changed entry re-cut and what that reopens asked on
    the profile's hosts, as an update asks it (`commands.update.run`), kept
    as an `edit` state. Unanswered, the edit stays pending, and the same
    command resumes it. `--dry-run`: the same on a scratch copy of the
    store, the update priced by its own dry run (`standard` by default)."""
    if asked.dry_run:
        with scratch_copy(asked.paper) as paper:
            scratch = replace(asked, paper=paper, dry_run=False)
            return _vocabulary_edit_on(scratch, edited, say, dry=True)
    return _vocabulary_edit_on(asked, edited, say)


def _vocabulary_edit_on(asked: Edit, edited: str, say: Say, dry: bool = False) -> int:
    """`_vocabulary_edit` on `asked`'s store; `dry`: its update a dry
    run."""
    if bool(asked.agent) != bool(asked.session):
        raise UsageError(
            "--agent and --agent-session go together: the model and its session"
        )
    author = edits.agent(asked.agent, asked.session) if asked.agent else edits.A_PERSON
    store = kept_store(asked.paper)
    kept = kept_on(asked.paper, store)
    given = asked.asking or Asking()
    if not dry and seating.hosted(given):
        # its budget taken before the map is filed: a paid edit refused for
        # it files nothing
        given = seating.take_budget(
            given,
            asked.paper.directory,
            asked.paper.store,
            asked.command,
            "asking a model needs --budget (--host none asks nothing: an edit "
            "that reopens anything waits, and the same command resumes it)",
            say,
            frozenset(a.key for a in versions.pending_answers(store, kept.version)),
        )
    with edits.opened(store, kept) as opened:
        recorded = edits.give_vocabulary(opened, edited, author, asked.reason)
        say.note(
            (f"axiomation edit: {DRY_RUN_HEAD}; " if dry else "axiomation edit: ")
            + f"the vocabulary map given, answer "
            f"{recorded.answer.key[:12]}; applied as an update of version "
            f"{kept.version[:12]} onto itself"
        )
        # the update's own flags: the edit's asking ones, its jobs, and,
        # where nothing says how to ask, nothing asked; a dry run priced
        # under the update's default profile
        asking = Asking(
            profile=given.profile,
            profiles=given.profiles,
            host=(
                "none"
                if not dry and given.profile is None and given.host is None
                else given.host
            ),
            model=given.model,
            effort=given.effort,
            rates=given.rates,
            budget=given.budget,
            expect=given.expect,
            timeout=given.timeout,
            jobs=given.jobs,
        )
        update = updating.Update(
            paper=Paper(Path(opened.directory), asked.paper.store, kept.id),
            root=Path(kept.root),
            dry_run=dry,
            asking=asking,
        )
        return updating.run(update, say, edited=kept).code


def change(asked: Change, say: Say) -> int:
    """`change PAPER [--fragment F] [--add|--retract|--keep|--standing OWNER
    LINE] [--part LEGEND]`: the lines applied to the kept state's
    edges sheet, then given as an edges edit (`_given`)."""
    text = asked.fragment
    if text is None and not any(
        (
            asked.add,
            asked.retract,
            asked.keep,
            asked.standing,
            asked.delta,
            asked.part,
        )
    ):
        raise UsageError(
            "give --fragment, or --add, --retract, --keep, --standing, --delta or "
            "--part"
        )

    def edit(kept: edits.Kept, shown: str) -> str:
        view = queries.View(kept.store, kept.state)

        def owner(ref: str) -> str:
            return view.handle(view.resolve(ref))

        fragment = edits.parse_fragment(text) if text is not None else edits.Fragment()
        # a Δ line's note is its reason
        if (
            any(
                op in (edits.RETRACT, edits.KEEP) and not line.startswith(es.DELTA_MARK)
                for _, op, line in fragment.lines
            )
            and not asked.reason
        ):
            raise UsageError("a retract or a keep needs --reason")
        lines = [(owner(o), op, line) for o, op, line in fragment.lines]
        lines += [(owner(o), edits.ADD, line) for o, line in asked.add]
        lines += [(owner(o), edits.RETRACT, line) for o, line in asked.retract]
        lines += [(owner(o), edits.KEEP, line) for o, line in asked.keep]
        for o, line in asked.standing:
            lines += edits.standing_lines(shown, owner(o), line)
        for o, line in asked.delta:
            lines += edits.delta_lines(shown, owner(o), line)
        legend = [*fragment.legend, *(f"  {x}" for x in asked.part)]
        given = edits.Fragment(tuple(legend), tuple(lines))
        for note in edges_file.restating_none(shown, given):
            say.note(f"axiomation change: note: {note}")
        return edits.applied_fragment(shown, given)

    # its author is shown no stale mark, so it clears none
    return _given(asked, Layer.EDGES, edit, say, marks_shown=False)


#: What a dry run's report opens with.
DRY_RUN_HEAD = "dry run on a scratch copy of the store: nothing recorded"


def _given(
    asked: Given,
    layer: Layer,
    edit: Callable[[edits.Kept, str], str],
    say: Say,
    marks_shown: bool = True,
) -> int:
    """An edit given on the kept state: the stage's file as the
    state shows it, edited by `edit`, checked by its stage's check and
    recorded by its author; refused, its problems said and nothing
    recorded. Then applied, a new state at once, said with what changed
    and what each changed owner's edges edit reaches; a cut, roles or
    groups edit asks what it reopens on the profile's hosts (`edit`'s
    `--profile` and `--budget`), and where that is not answered it says
    what is, and the same command resumes it. `--dry-run`: the same on a
    scratch copy of the store, asking nothing, a cut, roles or groups
    edit's questions priced under the profile (`standard` by default)."""
    if not asked.dry_run:
        return _given_on(asked, layer, edit, marks_shown, False, say)
    with scratch_copy(asked.paper) as paper:
        scratch = replace(asked, paper=paper)
        # an edges edit asks nothing: shown and given as the edit itself is
        if layer is not Layer.EDGES and scratch.asking is not None:
            profiled, _ = seating.default_profile(scratch.asking)
            scratch = replace(scratch, asking=profiled)
        return _given_on(scratch, layer, edit, marks_shown, True, say)


def _given_on(
    asked: Given,
    layer: Layer,
    edit: Callable[[edits.Kept, str], str],
    marks_shown: bool,
    dry: bool,
    say: Say,
) -> int:
    """`_given`'s edit on `asked`'s store; `dry`: seating no runner and
    pricing what it would ask."""
    if bool(asked.agent) != bool(asked.session):
        raise UsageError(
            "--agent and --agent-session go together: the model and its session"
        )
    author = edits.agent(asked.agent, asked.session) if asked.agent else edits.A_PERSON
    store = kept_store(asked.paper)
    kept = kept_on(asked.paper, store)
    asking = asked.asking
    profile = None if asking is None else seating.profile_of(asking)
    runner = None
    # an edges edit asks nothing: no host is seated for it
    if (
        profile is not None
        and layer is not Layer.EDGES
        and asking is not None
        and seating.hosted(asking)
        and not dry
    ):
        asking = seating.take_budget(
            asking,
            asked.paper.directory,
            asked.paper.store,
            asked.command,
            "asking a model needs --budget (--host none asks nothing: an edit "
            "that reopens anything waits, and the same command resumes it)",
            say,
            # the edit it resumes is its own: it never refuses it
            frozenset(a.key for a in versions.pending_answers(store, kept.version)),
        )
        agents = seating.seated(
            profile,
            ReviewSettings(stages=(Stage.ROLES, Stage.EDGES), given_cut=True),
            priced=True,
            hosted=True,
        )
        runner = seating.runner(
            asking,
            profile,
            seating.seats_of(agents, seating.executables_of(agents)),
            store,
        )
    settings = None if profile is None else review_settings(profile)
    with edits.opened(store, kept, settings) as opened:
        shown = edits.shown(opened, layer)
        _kept_notes(asked.command, opened, layer, say)
        edited = edit(opened, shown)
        given = edits.give(
            opened, layer, edited, author, asked.reason, runner, marks_shown
        )
        if given.problems:
            problems = (
                edits.merged_edges(given.problems, shown, edited)
                if layer is Layer.EDGES
                else given.problems
            )
            listed = "\n".join(f"- {problem}" for problem in problems)
            say.note(
                f"axiomation {asked.command}: refused, nothing recorded:\n{listed}"
            )
            return 1
        assert given.recorded is not None
        recorded = given.recorded
        spent = round(sum(r.spent for r in given.reports), 6)
        if given.state is None:
            after = corrected_cut(opened.document, opened.cut, recorded.corrections).cut
            unanswered = ", ".join(
                f"{q.key[:12]} ({q.stage.value})" for q in given.unanswered
            )
            report = queries.Report(
                head=[
                    f"recorded {layer.value} edit {recorded.answer.key[:12]} by "
                    f"{author.host} on state {kept.id}: not whole, no state made: "
                    f"unanswered {unanswered}; the same command resumes it"
                    + ("" if runner else " (give --profile and --budget to ask)")
                ],
                data={
                    "recorded": recorded.answer.key,
                    "state": None,
                    "unanswered": [q.key for q in given.unanswered],
                    "spent": spent,
                },
            )
            if dry:
                assert profile is not None
                priced = _edit_estimate(profile, given.unanswered)
                total = round(sum(cost for _, _, cost in priced), 6)
                report.head = [
                    DRY_RUN_HEAD,
                    f"a {layer.value} edit on state {kept.id}: it would ask "
                    f"{len(priced)} question(s), estimated ${total:.2f} under "
                    f"{f'profile {profile.name}' if profile.name else 'the flags'}; "
                    "what their answers reopen next is "
                    "priced once they are answered",
                ]
                report.data = {"dry_run": True, "state": None, "estimate": total}
                report.sections["asks"] = [
                    queries.Item(
                        f"{q.key[:12]} ({q.stage.value}, {agent.model}): ${cost:.2f}",
                        {
                            "key": q.key,
                            "stage": q.stage.value,
                            "model": agent.model,
                            "estimate": cost,
                        },
                    )
                    for q, agent, cost in priced
                ]
            report.sections["changes"] = [
                queries.Item(
                    f"{c.layer.value} {c.change.value}: {', '.join(c.before)} → "
                    f"{', '.join(c.after)}",
                    {
                        "layer": c.layer.value,
                        "change": c.change.value,
                        "before": list(c.before),
                        "after": list(c.after),
                    },
                )
                for c in graph_changes(
                    GraphState(opened.cut, EdgeRecords()),
                    GraphState(after, EdgeRecords()),
                )
            ]
            say.write(queries.render(report, asked.limit, asked.json))
            if dry:
                return 0
            stopped = next((r for r in given.reports if r.stopped is not None), None)
            if stopped is not None:
                return Exit.BREACH if stopped.stopped is Stop.BREACH else Exit.STOPPED
            return Exit.UNANSWERED
        touched = (
            list(dict.fromkeys(p.owner for p in person.edge_pins(shown, edited)))
            if layer is Layer.EDGES
            else []
        )
    made = given.state
    old, new = queries.View(store, kept), queries.View(store, made)
    report = queries.changes_report(store, old, new)
    report.head = [
        f"state {made.id}: an edit of state {kept.id} by {author.host}"
        + (f" {author.model} (session {author.session})" if author.session else "")
        + f", answer {recorded.answer.key[:12]}"
        + (f", ${spent:.2f} spent" if given.reports else "")
    ]
    report.data |= {"state": made.id, "recorded": recorded.answer.key}
    if given.reports:
        report.data["spent"] = spent
    if dry:
        report.head = [DRY_RUN_HEAD, *report.head]
        report.data["dry_run"] = True
    report.sections.pop("text", None)
    report.sections["gates"] = check.delta(
        check.verdict(old, asked.paper.directory),
        check.verdict(new, asked.paper.directory),
    )
    report.sections["marks"] = queries.marks_after_edit(old, new, touched, marks_shown)
    by_handle = {handle: ident for ident, handle in new.handles.items()}
    for handle in touched:
        if handle in by_handle:
            reached = queries.reach_report(new, by_handle[handle])
            report.sections[f"reach of {handle}"] = reached.sections["reached"]
    say.write(queries.render(report, asked.limit, asked.json))
    return 0


def _edit_estimate(
    profile: Profile, questions: Sequence[Question]
) -> list[tuple[Question, Agent, float]]:
    """Each question an edit would ask, its stage's agent under `profile`,
    and its estimate at that agent's rates."""
    stages = tuple(dict.fromkeys(Stage(q.stage) for q in questions))
    agents = seating.seated(
        profile,
        ReviewSettings(stages=stages, given_cut=True),
        priced=True,
        hosted=False,
    )
    found = []
    for q in questions:
        agent = agents[Stage(q.stage)]
        assert agent.rates is not None
        found.append(
            (
                q,
                agent,
                estimate(
                    q.prompt, agent.rates, q.workspace, seating.harness_tokens(agent)
                ),
            )
        )
    return found


def _sheet_arguments(commands: Any) -> None:
    sheet = commands.add_parser(
        "sheet",
        help="write a stage's file as a kept state shows it, for a person or an "
        "agent to edit",
    )
    kept_arguments(sheet)
    _stage_argument(sheet)
    seating.asking_arguments(sheet, None, "the sheet")
    output_argument(sheet)


def _edit_arguments(commands: Any) -> None:
    edit = commands.add_parser(
        "edit",
        help="give an edit of a stage's file on a kept state, a new state once "
        "answered: a cut, roles or groups edit asks what it reopens; unanswered, "
        "it stays pending, no state made, and the same command resumes it",
    )
    kept_arguments(edit)
    _stage_argument(edit)
    edit.add_argument("--file", type=Path, required=True, help="the edited file")
    seating.asking_arguments(
        edit, "the questions a cut, roles or groups edit reopens", "the edit"
    )
    _author_arguments(edit)
    _dry_run_argument(edit, "the questions it reopens, priced")


def _change_arguments(commands: Any) -> None:
    change = commands.add_parser(
        "change",
        help="change a kept state's edges by a few sheet lines, a new state at "
        "once: add, retract or keep a line, set a standing",
    )
    kept_arguments(change)
    change.add_argument(
        "--fragment",
        type=Path,
        help="a file of sheet lines to add, retract or keep (`-`: stdin; the "
        "line format: docs/guide.md § 8)",
    )
    for flag, what in (
        ("--add", "add LINE, an edge or standing line, under OWNER"),
        (
            "--retract",
            "take LINE off OWNER, as `show` prints it, its ` — <why>` tail "
            "optional (needs --reason)",
        ),
        (
            "--keep",
            "keep LINE under OWNER as it stands, pinned: an agent's later edit "
            "may not change it; LINE as for --retract (needs --reason)",
        ),
        (
            "--standing",
            "set OWNER's standing on a claim to LINE, replacing the one it had "
            "(the line format: docs/guide.md § 8)",
        ),
        (
            "--delta",
            "correct what an update said changed in OWNER's block to LINE, on "
            "an update's state (the line format: docs/guide.md § 8)",
        ),
    ):
        change.add_argument(
            flag,
            nargs=2,
            action="append",
            default=[],
            metavar=("OWNER", "LINE"),
            help=f"{what}; OWNER a handle, node id, block id or a handle's prefix; "
            "repeat for several",
        )
    change.add_argument(
        "--part",
        action="append",
        default=[],
        metavar="LEGEND",
        help='add a part, `<target> [<label>]: "<words>"`; repeat for several',
    )
    _author_arguments(change)
    _dry_run_argument(change, "")


#: The edits' arguments, by command.
ARGUMENTS: dict[str, Callable[[Any], None]] = {
    "sheet": _sheet_arguments,
    "edit": _edit_arguments,
    "change": _change_arguments,
}


def _stage_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--stage",
        dest="layer",
        required=True,
        choices=[*(layer.value for layer in person.STAGES), VOCABULARY_STAGE],
        help="which stage's sheet: the blocks (cut), their roles, groups or "
        "edges, or the vocabulary map",
    )


def _author_arguments(command: argparse.ArgumentParser) -> None:
    """Who gives an edit, why, and how its result prints."""
    command.add_argument(
        "--agent",
        metavar="MODEL",
        help="an agent's edit, by this model: it pins no line, and may not "
        "change a line a person pinned (default: a person's)",
    )
    command.add_argument(
        "--agent-session",
        dest="session",
        metavar="ID",
        help="the agent's session id, recorded with its edit",
    )
    command.add_argument("--reason", help="why, recorded with the edit")
    command.add_argument(
        "--limit",
        type=int,
        default=queries.LIMIT,
        help=f"items a section shows (default {queries.LIMIT}; 0: all)",
    )
    command.add_argument("--json", action="store_true", help="JSON, not lines")


def _dry_run_argument(command: argparse.ArgumentParser, also: str) -> None:
    command.add_argument(
        "--dry-run",
        action="store_true",
        help="show what it would do and keep nothing: its lines, what lapses, "
        "the gates" + (f"; {also}" if also else ""),
    )
