"""`build` and `review-cut`: a paper's graph asked stage by stage, every
stage in order for a build, the stages named for a review of the cut;
the corrected cut exported, a build's state kept, and the asks a build
ends with."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from axiomation import develop, versions
from axiomation.commands import (
    DRY_RUN_HELP,
    STORE_HELP,
    SUMMARY_JSON_HELP,
    Exit,
    Paper,
    Say,
    UsageError,
    asks,
    dollars,
    given_export,
    output_argument,
    own_store,
    paper_arguments,
    paper_of,
    prompts_argument,
    questions_words,
    requested,
    said,
    seating,
    state_summary,
    written,
)
from axiomation.commands.seating import Asking
from axiomation.cutter import Cut, CutRules, DisplayRule, cut
from axiomation.driver import (
    DROPPED,
    DROPPED_TWICE,
    PERSON_NO_CUT,
    Mapped,
    PersonRounds,
    Proposal,
    Review,
    ReviewSettings,
    decided_by,
    map_vocabulary,
    planned_questions,
    review_cut,
    review_payload,
    with_vocabulary,
)
from axiomation.exchange import ExchangeError, cut_from_payload, dumps, vocabulary_of
from axiomation.person import Pin
from axiomation.profiles import BUILD_STAGES, Agent, Harness, Profile
from axiomation.questions import Question, Stage
from axiomation.questions.edge_sheet import sheet_files
from axiomation.reader import Document, Vocabulary, document_hash, read_paper
from axiomation.records import EdgeRecords, EntryKind, RecordStore, holds
from axiomation.resolver import Resolver
from axiomation.run_state import RunFile, made_from
from axiomation.runner import Runner, Stop
from axiomation.runner.claude_code import EFFORTS
from axiomation.runner.hosts import estimate
from axiomation.versions import Command

#: A build's questions asked at a time.
BUILD_JOBS = 4


@dataclass(frozen=True, kw_only=True)
class Build:
    """`build` (`command` build) or `review-cut` (the `stage` named): the
    paper, its store, the cut a later stage starts from (`base_cut`), what
    a dry run writes (`prompts`), and who answers."""

    command: str
    paper: Paper
    root: Path | None = None
    displays: str = DisplayRule.IN_PARAGRAPH.value
    stage: str = "boundaries"
    base_cut: Path | None = None
    dry_run: bool = False
    json: bool = False
    prompts: Path | None = None
    no_search: bool = False
    no_place: bool = False
    no_circles: bool = False
    output: Path | None = None
    #: Develop mode: the store's answers folded again, asking nothing and
    #: keeping no state, as an audit of the kept states.
    replay: bool = False
    asking: Asking = field(default_factory=lambda: Asking(jobs=BUILD_JOBS))


def request(arguments: argparse.Namespace) -> Build:
    """`build`'s or `review-cut`'s request from its parsed arguments."""
    return requested(
        Build, arguments, paper=paper_of(arguments), asking=seating.asking(arguments)
    )


def run(asked: Build, say: Say) -> int:
    given, defaulted = (
        seating.default_profile(asked.asking)
        if asked.command == "build"
        else (asked.asking, False)
    )
    profile = seating.profile_of(given)
    settings = review_settings_of(asked, profile, asked.command == "build")
    hosted = seating.hosted(given)
    if asked.replay and hosted:
        raise UsageError("--replay asks nothing: give --host none")
    if asked.replay and not develop.on():
        raise UsageError(
            "--replay reads the filed answers' texts again, which only develop "
            f"mode keeps: a develop tool, run with {develop.ENV}=1"
        )
    asking = hosted and not asked.dry_run
    if asked.command == "build" and versions.keeps_state(asked.paper.store):
        # a build keeps a state: refused before it maps or prices anything
        versions.refuse_apart(RecordStore(asked.paper.store))
    document, found, settings = review_input(asked, settings)
    if (
        asked.command == "build"
        and not asked.replay
        and not asked.dry_run
        and asked.prompts is None
    ):
        # a built version asks nothing: neither its map nor a budget (its
        # prompts, asked for, are written from the map as the build read it)
        own_store(asked.paper)
        store = RecordStore(asked.paper.store)
        kept_whole = versions.built(store, document_hash(document))
        if kept_whole is not None:
            run = RunFile(
                store.run_directory,
                asked.command,
                document_hash(document),
                made_from(settings),
            )
            return _built(asked, store, kept_whole, made_from(settings), run, say)
    if (
        asked.command == "build"
        and not asked.dry_run
        and versions.keeps_state(asked.paper.store)
    ):
        # a new state, on none: never beside one waiting for the person's
        # word; a version built already asks nothing
        versions.refuse_beside_waiting(RecordStore(asked.paper.store), None)
    if asking:
        given = seating.take_budget(
            given,
            asked.paper.directory,
            asked.paper.store,
            asked.command,
            seating.budget_needed(given, asked.command, defaulted),
            say,
        )
    agents = seating.seated(
        profile, settings, priced=asking or asked.dry_run, hosted=hosted
    )
    executables: dict[Harness, str] = {}
    if asking:
        for harness in {agent.harness for agent in agents.values()}:
            command = seating.EXECUTABLES[harness]
            found_executable = shutil.which(command)
            if found_executable is None:
                raise UsageError(f"--host {harness.value} needs `{command}` on PATH")
            executables[harness] = found_executable
    if asked.dry_run:
        dry = _dry_run(asked, document, found, settings, profile, agents)
        say.line(json.dumps(dry, indent=2) if asked.json else _dry_run_lines(dry))
        return 0
    if not asking and not holds(asked.paper.store, EntryKind.ANSWER):
        raise UsageError(
            f"--host none replays a store, and {asked.paper.store} holds no answers"
        )
    # the files the run reads, kept with its state once it is whole
    files = (
        versions.snapshot(asked.paper.directory, document)
        if asked.command == "build" and not asked.replay
        else None
    )
    own_store(asked.paper)
    store = RecordStore(asked.paper.store)
    # the run's working state and the texts of the answers it asked,
    # resumed by the same command; a replay asks nothing, and reads the
    # texts develop mode kept
    run = (
        None
        if asked.replay
        else RunFile(
            store.run_directory,
            asked.command,
            document_hash(document),
            made_from(settings),
        )
    )
    runner = None
    if asking:
        seats = seating.seats_checked(agents, executables, document, found, settings)
        first = next(iter(seats.values()))
        runner = Runner(
            first.host,
            Resolver(store, run=run),
            given.budget,
            first.rates,
            jobs=given.jobs,
            timeout=profile.session_timeout_seconds,
            transcripts=store.transcripts,
            seats=seats,
            profile=profile.name,
            expect=given.expect,
        )
    mapped = Mapped(Vocabulary())
    if Stage.VOCABULARY not in settings.stages and not settings.given_cut:
        # a review of chosen stages reads the paper as its store's build
        # read it, and asks no map of its own
        found_map = map_vocabulary(document, store, settings.templates)
        if found_map.vocabulary is not None and found_map.vocabulary != Vocabulary():
            document, found, settings = review_input(
                asked, settings, found_map.vocabulary
            )
    if Stage.VOCABULARY in settings.stages:
        mapped = map_vocabulary(document, store, settings.templates, runner)
        if mapped.vocabulary is None:
            # every later stage reads the cut, and the cut reads the map
            say.line(said(unmapped_summary(profile, mapped, store), asked.json))
            stopped = next((r for r in mapped.reports if r.stopped is not None), None)
            if stopped is not None and stopped.stopped is Stop.BREACH:
                return Exit.BREACH
            return Exit.STOPPED if stopped is not None else Exit.UNANSWERED
        if mapped.vocabulary != Vocabulary():
            document, found, settings = review_input(asked, settings, mapped.vocabulary)
    if asked.prompts is not None:
        write_prompts(asked.prompts, planned_questions(document, found, settings))
    if files is not None and run is not None and asked.prompts is not None:
        kept_whole = versions.built(store, document_hash(document))
        if kept_whole is not None:
            return _built(asked, store, kept_whole, made_from(settings), run, say)
    review = review_cut(document, found, store, settings, runner, run)
    review = with_vocabulary(review, mapped, store)
    exported = dumps(review_payload(document, review, settings))
    if asked.output is not None:
        written(exported, asked.output, say)
    summary = {"profile": profile.name, **_summary(review, store)}
    if files is None and run is not None and review.complete and review.stopped is None:
        # `review-cut` keeps no state: its run ends with it
        run.kept()
    if files is not None and review.complete and review.stopped is None:
        kept, _ = versions.record(
            store,
            files,
            document_hash(document),
            document.root,
            exported.encode("utf-8"),
            Command.BUILD,
            answers=[r.key for r in review.person.applied],
            made_from=None if run is None else run.state.cursor.made_from,
        )
        if run is not None:
            run.kept()
        summary["state"] = state_summary(asked.paper, store, kept)
        last = kept
        if (
            Stage.EDGES in settings.stages
            and profile.search_support_after_edges
            and not asked.no_search
        ):
            try:
                searched = asks.support_after(
                    store, kept, profile, given.profiles, runner
                )
            except BaseException:
                # the state is kept and paid for: its summary is said whatever
                # the search raises
                say.line(said(summary, asked.json))
                raise
            if searched is not None:
                summary["support"], support_stop = searched
                summary["spent"] = {
                    "run": round(
                        summary["spent"]["run"] + summary["support"].get("spent", 0.0),
                        6,
                    ),
                    "store": round(sum(a.cost for a in store.answers()), 6),
                }
                if support_stop is Stop.BREACH:
                    say.line(said(summary, asked.json))
                    return Exit.BREACH
                if "state" in summary["support"]:
                    last = versions.state(store, summary["support"]["state"])
        searched_whole = not {"unanswered", "refused"} & set(summary.get("support", {}))
        if (
            Stage.EDGES in settings.stages
            and profile.place_lines_after_edges
            and not asked.no_place
            and searched_whole
        ):
            try:
                # after the search, before the circles, which read what the
                # placed lines change of what claims rest on
                placed = asks.place_after(
                    store,
                    last,
                    profile,
                    given.profiles,
                    runner,
                    agents[Stage.EDGES],
                )
            except BaseException:
                # the state is kept and paid for: its summary is said whatever
                # the placing raises
                say.line(said(summary, asked.json))
                raise
            if placed is not None:
                summary["place"], place_stop = placed
                summary["spent"] = {
                    "run": round(
                        summary["spent"]["run"] + summary["place"].get("spent", 0.0),
                        6,
                    ),
                    "store": round(sum(a.cost for a in store.answers()), 6),
                }
                if place_stop is Stop.BREACH:
                    say.line(said(summary, asked.json))
                    return Exit.BREACH
                if "state" in summary["place"]:
                    last = versions.state(store, summary["place"]["state"])
        placed_whole = not {"unanswered", "refused"} & set(summary.get("place", {}))
        if (
            Stage.EDGES in settings.stages
            and profile.ask_circles_after_edges
            and not asked.no_circles
            and searched_whole
            and placed_whole
        ):
            try:
                # after the search and the placing, whose lines may close or
                # break a circle
                circled = asks.circles_after(
                    store,
                    last,
                    profile,
                    given.profiles,
                    runner,
                    agents[Stage.EDGES],
                )
            except BaseException:
                # the state is kept and paid for: its summary is said whatever
                # the question raises
                say.line(said(summary, asked.json))
                raise
            if circled is not None:
                summary["circles"], circles_stop = circled
                # the run's whole cost, the question's beside it
                summary["spent"] = {
                    "run": round(
                        summary["spent"]["run"] + summary["circles"].get("spent", 0.0),
                        6,
                    ),
                    "store": round(sum(a.cost for a in store.answers()), 6),
                }
                if circles_stop is Stop.BREACH:
                    say.line(said(summary, asked.json))
                    return Exit.BREACH
    say.line(said(summary, asked.json))
    if "unanswered" in summary.get("support", {}):
        say.note(
            f"axiomation build: state {summary['state']['id']} is kept; the search "
            "for its claims' support is not answered: `axiomation support PAPER "
            "--ask` asks it, then `axiomation place PAPER --ask` and `axiomation "
            "circles PAPER --ask`"
        )
    if "unanswered" in summary.get("place", {}):
        say.note(
            f"axiomation build: state {summary['state']['id']} is kept; the placing "
            "of its blocks' lines is not answered: `axiomation place PAPER --ask` "
            "asks it, then `axiomation circles PAPER --ask`"
        )
    if "unanswered" in summary.get("circles", {}):
        say.note(
            f"axiomation build: state {summary['state']['id']} is kept; the question "
            "on its circles is not answered: `axiomation circles PAPER --ask` asks it"
        )
    stopped = review.stopped
    if stopped is None:
        return 0
    return Exit.BREACH if stopped.stopped is Stop.BREACH else Exit.STOPPED


def _built(
    asked: Build,
    store: RecordStore,
    kept: versions.State,
    made: str,
    run: RunFile,
    say: Say,
) -> int:
    """A build of a version already built: its latest whole state, asked
    nothing. A whole state is never replaced because the build's settings
    changed; what continues from it is a step (`docs/guide.md`)."""
    # a run that kept this state and stopped before removing its file
    run.kept()
    if asked.output is not None:
        written(versions.kept_export(store, kept).decode("utf-8"), asked.output, say)
    summary = {"built": True, "state": state_summary(asked.paper, store, kept)}
    say.line(said(summary, asked.json))
    if kept.made_from is not None and kept.made_from != made:
        say.note(
            f"axiomation build: state {kept.id} of this version is kept, made "
            "under other settings, and stands: `axiomation review`, `reread`, "
            "`support`, `place` and `circles` continue from it, `edit` changes it; a "
            "build under these settings from nothing is a store of its own "
            "(--store)"
        )
    if versions.pending_answers(store, kept.version):
        say.note(
            "axiomation build: an edit given on this version is not applied "
            "yet: `axiomation edit` resumes it"
        )
    return 0


def review_settings_of(asked: Build, profile: Profile, build: bool) -> ReviewSettings:
    """`review-cut`'s and `build`'s settings from the profile and the flags:
    the stages (build's every stage, a given cut's from its
    roles), the passes and rounds, the templates."""
    passes = (
        profile.boundary_passes,
        profile.role_passes,
        profile.group_passes,
        profile.edge_passes,
    )
    if min(passes) < 1 or profile.context_blocks < 0 or profile.cut_rounds < 0:
        raise UsageError(
            "--boundary-passes, --role-passes, --group-passes and --edge-passes "
            "take 1 or more, --overlap and --cut-rounds 0 or more"
        )
    if profile.decide_passes not in (0, 1):
        raise UsageError("--decide-passes takes 0 or 1")
    given = asked.base_cut is not None
    if build:
        # A given cut's boundaries are settled: the build starts at its roles.
        stages = tuple(s for s in BUILD_STAGES if not (given and s is Stage.BOUNDARIES))
    else:
        stages = _stages(asked.stage)
    if given and Stage.BOUNDARIES in stages:
        raise UsageError(
            "--cut gives a cut whose boundaries are settled: give --stage roles, "
            "--stage groups or --stage edges"
        )
    settings = ReviewSettings(
        profile.chunk_characters,
        profile.context_blocks,
        profile.boundary_passes,
        judge=profile.judge_boundaries,
        stages=stages,
        outline=profile.show_outline,
        role_passes=profile.role_passes,
        group_passes=profile.group_passes,
        edge_passes=profile.edge_passes,
        cut_rounds=profile.cut_rounds,
        decide_passes=profile.decide_passes,
        show_rule_flags_in_edge_review=profile.show_rule_flags_in_edge_review,
        given_cut=given,
        templates=profile.templates,
    )
    return settings


def review_input(
    asked: Build,
    settings: ReviewSettings,
    vocabulary: Vocabulary | None = None,
) -> tuple[Document, Cut, ReviewSettings]:
    """The paper, read with its vocabulary map where it has one (with
    `--cut`, the map that export was read with), the cut a review starts
    from (the cutter's, or `--cut`'s) and the settings with the paper's
    files where the edges are asked."""
    if vocabulary is None and settings.given_cut:
        vocabulary = vocabulary_of(given_export(asked.base_cut))
    document = read_paper(asked.paper.directory, asked.root, vocabulary)
    if Stage.EDGES in settings.stages:
        files = sheet_files(asked.paper.directory, document.never_included)
        settings = replace(settings, files=tuple(files))
    rules = CutRules(DisplayRule(asked.displays))
    found = (
        _base_cut(
            asked.base_cut,
            document,
            rules,
            roles=Stage.ROLES not in settings.stages,
        )
        if settings.given_cut
        else cut(document, rules)
    )
    return document, found, settings


def _stages(value: str) -> tuple[Stage, ...]:
    if value == "both":
        return (Stage.BOUNDARIES, Stage.GROUPS)
    return (Stage(value),)


def _base_cut(
    path: Path, document: Document, rules: CutRules, roles: bool = False
) -> Cut:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise UsageError(f"--cut: {path} is not JSON: {error}") from None
    if not isinstance(payload, dict):
        raise UsageError(f"--cut: {path} is not an exchange file")
    try:
        return cut_from_payload(document, payload, rules, roles)
    except ExchangeError as error:
        raise UsageError(f"--cut: {path}: {error}") from None


def write_prompts(directory: Path, planned: dict[Stage, tuple[Question, ...]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for stage, questions in planned.items():
        for n, question in enumerate(questions, 1):
            (directory / f"{stage.value}-{n}.md").write_text(
                question.prompt, encoding="utf-8"
            )
            if question.workspace is not None:
                files = directory / f"{stage.value}-{n}"
                files.mkdir(exist_ok=True)
                (files / "brief.md").write_text(
                    question.workspace.instructions, encoding="utf-8"
                )
                for name, text in question.workspace.files.items():
                    (files / name).write_text(text, encoding="utf-8")


def _answered(store: Path) -> set[str]:
    """The keys with an authoritative answer in a store, read without
    creating it."""
    if not holds(store, EntryKind.ANSWER):
        return set()
    return set(RecordStore(store).authoritative())


def _dry_run(
    asked: Build,
    document: Document,
    found: Cut,
    settings: ReviewSettings,
    profile: Profile,
    agents: dict[Stage, Agent],
) -> dict[str, Any]:
    planned = planned_questions(document, found, settings)
    if asked.prompts is not None:
        write_prompts(asked.prompts, planned)
    answered = _answered(asked.paper.store)
    stages = {}
    total = 0.0
    for stage, questions in planned.items():
        unanswered = [q for q in questions if q.key not in answered]
        rates = agents[stage].rates
        assert rates is not None
        cost = sum(
            estimate(
                q.prompt, rates, q.workspace, seating.harness_tokens(agents[stage])
            )
            for q in unanswered
        )
        total += cost
        sizes = [shown(q) for q in questions]
        stages[stage.value] = {
            "questions": len(questions),
            "answered": len(questions) - len(unanswered),
            "prompt_chars": {"total": sum(sizes), "largest": max(sizes, default=0)},
            "estimate": round(cost, 4),
        }
    return {
        "dry_run": True,
        "profile": profile.name,
        "seats": {
            stage.value: seating.described(agent) for stage, agent in agents.items()
        },
        "chunk": "paper" if settings.chunk is None else settings.chunk,
        "overlap": settings.overlap,
        "rounds": settings.rounds,
        "judge": settings.judge,
        "outline": settings.outline,
        "stages": stages,
        "estimate": round(total, 4),
        "note": "one attempt a question, a session's files read once; the "
        "first author pass only, the judge's questions (with --judge) on the "
        "uncorrected cut; the roles questions (every pass), the groups "
        "question and the edges sessions (every pass, a later one on the sheet as "
        "rendered) priced on the uncorrected cut",
    }


def _dry_run_lines(dry: Mapping[str, Any]) -> str:
    """A dry run as a person reads it: a line a stage, its questions, price
    and seat, then the estimate; its note, what it counts, is the JSON's."""
    width = max((len(stage) for stage in dry["stages"]), default=0)
    lines = [f"priced, nothing asked (profile {dry['profile']}):"]
    for stage, found in dry["stages"].items():
        seat = dry["seats"][stage]
        effort = f", {seat['effort']}" if seat["effort"] else ""
        lines.append(
            f"  {stage:<{width}}  {questions_words(found)}, "
            f"{dollars(found['estimate'])}  ({seat['model']}{effort})"
        )
    lines.append(
        f"estimate {dollars(dry['estimate'])}, each question asked once "
        "(--json names what it counts)"
    )
    return "\n".join(lines)


def shown(question: Question) -> int:
    """The characters a question shows: its prompt, and a session's brief
    and files."""
    shown = len(question.prompt)
    if question.workspace is not None:
        shown += len(question.workspace.instructions)
        shown += sum(len(text) for text in question.workspace.files.values())
    return shown


def unmapped_summary(
    profile: Profile, mapped: Mapped, store: RecordStore
) -> dict[str, Any]:
    """A build that stopped at its vocabulary map: the map's questions left
    unanswered, its failures and what it spent."""
    answered = store.authoritative()
    stopped = next((r for r in mapped.reports if r.stopped is not None), None)
    return {
        "profile": profile.name,
        "vocabulary": {
            "unanswered": [q.key for q in mapped.questions if q.key not in answered],
            "failed": [k for r in mapped.reports for k in r.failed],
        },
        "spent": {"run": round(sum(r.spent for r in mapped.reports), 6)},
        "stopped": stopped.stopped.value if stopped else None,
        "detail": stopped.detail if stopped else "",
        "complete": False,
    }


def _summary(review: Review, store: RecordStore) -> dict[str, Any]:
    failed = []
    stage_of = {q.key: (s, q) for s, qs in review.questions.items() for q in qs}
    for key in review.failed:
        stage, question = stage_of[key]
        first = question.handles.get("b1")
        failed.append({"key": key, "stage": stage.value, "first_block": first})
    answered = store.authoritative()
    stopped = review.stopped
    # A store's answers may come from a run under another profile.
    answered_by = Counter(
        answered[q.key].profile or "none"
        for qs in review.questions.values()
        for q in qs
        if q.key in answered
    )
    return {
        "questions": {s.value: len(qs) for s, qs in review.questions.items()},
        "answered": {
            s.value: sum(q.key in answered for q in qs)
            for s, qs in review.questions.items()
        },
        "answered_by": dict(sorted(answered_by.items())),
        "asked": sum(len(r.asked) for r in review.reports),
        "attempts": sum(r.attempts for r in review.reports),
        "failed": failed,
        "unanswered": list(review.unanswered),
        "spent": {
            "run": round(sum(r.spent for r in review.reports), 6),
            "store": round(sum(a.cost for a in store.answers()), 6),
        },
        "stopped": stopped.stopped.value if stopped else None,
        "detail": stopped.detail if stopped else "",
        "complete": review.complete,
        "applied": len(review.corrected.applied),
        "problems": len(review.corrected.problems),
        **(
            {
                "edges": _edge_counts(review.edges),
                # Each answered pass's and re-ask's, first to last: the edge
                # half is the last's.
                "edges_per_pass": [
                    _edge_counts(store.edge_records_of(q.key))
                    for q in review.questions[Stage.EDGES]
                    if q.key in answered
                ],
            }
            if Stage.EDGES in review.questions
            else {}
        ),
        **(
            {"decide": _decide_counts(review)}
            if Stage.DECIDE in review.questions
            else {}
        ),
        **(
            {"cut_rounds": _round_counts(review)}
            if review.cut_rounds or any(_a_round_s(p) for p in review.proposals)
            else {}
        ),
        **({"person": person_counts(review.person)} if review.person else {}),
    }


def person_counts(rounds: PersonRounds, carried: Sequence[Pin] = ()) -> dict[str, Any]:
    """The person rounds: answers applied and stale, the
    round still to ask, and the pins by state."""
    states = Counter(
        "lapsed" if not p.held else "held" for p in (*carried, *rounds.pins)
    )
    return {
        "applied": len(rounds.applied),
        "stale": len(rounds.stale),
        "pending": rounds.pending.key if rounds.pending else None,
        "pins": {"held": states["held"], "lapsed": states["lapsed"]},
    }


def _decide_counts(review: Review) -> dict[str, int]:
    """The deciding step's corrections: proposed, applied,
    dropped as a reversal, applied as a reversal, and the flags it was
    shown (the roles', the groups' and the two-block rule's) and acted
    on."""
    weighed = [p for p in review.proposals if p.stage == Stage.DECIDE.value]
    shown = review.decide_flags
    ids = {p.correction.id for p in weighed if p.applied}
    return {
        "proposed": len(weighed),
        "applied": sum(p.applied for p in weighed),
        "dropped": sum(
            (p.problem or "").startswith((DROPPED, DROPPED_TWICE)) for p in weighed
        ),
        "reversed": sum(bool(p.reverses) for p in weighed if p.applied),
        "flags_shown": len(shown),
        "flags_acted_on": sum(decided_by(r, weighed) in ids for r in shown),
    }


def _a_round_s(proposal: Proposal) -> bool:
    """Whether a proposal is a round of cut corrections', not the deciding
    step's nor a person round's: the person's own
    corrections, and its re-ask's cut lines, listed and not applied)."""
    return (
        proposal.stage != Stage.DECIDE.value
        and proposal.by is None
        and proposal.problem != PERSON_NO_CUT
    )


def _round_counts(review: Review) -> list[dict[str, int]]:
    """Each round's cut corrections: proposed, applied, refused
    by the assembler or dropped as a reversal, and the sheet's blocks the
    round's re-ask reopened."""
    reopened = {r.number: len(r.reopened) for r in review.cut_rounds}
    rounds = [p for p in review.proposals if _a_round_s(p)]
    found = []
    # A round may weigh no proposal and ask the roles of flagged blocks alone.
    for number in sorted({p.round for p in rounds} | set(reopened)):
        weighed = [p for p in rounds if p.round == number]
        found.append(
            {
                "round": number,
                "proposed": len(weighed),
                "applied": sum(p.applied for p in weighed),
                "dropped": sum(
                    (p.problem or "").startswith((DROPPED, DROPPED_TWICE))
                    for p in weighed
                ),
                "reopened": reopened.get(number, 0),
            }
        )
    return found


def _edge_counts(records: EdgeRecords) -> dict[str, int]:
    return {
        "entries": len(records.entries),
        "edges": len(records.edges),
        "self_marks": len(records.selfs),
        "standings": len(records.standings),
        "frame": len(records.frame),
    }


def _review_cut_command(commands: Any) -> None:
    _review_arguments(
        commands.add_parser(
            "review-cut",
            help="ask one stage of the build alone (the block boundaries by "
            "default), record the answers, and write the corrected cut",
        ),
        staged=True,
    )


def _build_command(commands: Any) -> None:
    _review_arguments(
        commands.add_parser(
            "build",
            help="build a paper's whole graph: its blocks, their roles, groups "
            "and edges, each stage working from the one before",
        ),
        staged=False,
    )


#: The arguments of `build` and `review-cut`, by command.
ARGUMENTS: dict[str, Callable[[Any], None]] = {
    "review-cut": _review_cut_command,
    "build": _build_command,
}


def _review_arguments(review: argparse.ArgumentParser, staged: bool) -> None:
    """`review-cut`'s arguments; `build`'s are the same but `--stage`. The
    tuning flags are hidden from `--help`: most are profile values a flag
    overrides (`docs/guide.md` § 2 names them)."""
    paper_arguments(review)
    review.add_argument("--store", type=Path, help=STORE_HELP)
    review.add_argument(
        "--profile",
        help="the recipe: which agent answers each stage, and the build's "
        "settings: standard, cheap (on OpenCode, experimental), or one of "
        "--profiles"
        + (
            ""
            if staged
            else f" (default: {seating.DEFAULT_PROFILE}, unless --host is given)"
        ),
    )
    review.add_argument(
        "--profiles", type=Path, help="a TOML file of profiles of your own"
    )
    if staged:
        review.add_argument(
            "--stage",
            choices=["boundaries", "roles", "groups", "edges", "both"],
            default="boundaries",
            help="the stage to ask: the block boundaries (the default), the "
            "roles, the groups, or the edges and standings; both: the "
            "boundaries, then the groups",
        )
    review.add_argument(
        "--host",
        choices=[*(h.value for h in Harness), "none"],
        help="the host of every stage; none asks no model: the graph from what "
        "the store already answered (free)"
        + ("; the default without --profile" if staged else "")
        + f"; {seating.OPENCODE_HELP}",
    )
    review.add_argument(
        "--model",
        help="the host's model, for every stage: Claude Code's name for it, or "
        "<provider>/<id> as OpenCode takes it (default: the profile's, else the "
        "host's default)",
    )
    review.add_argument(
        "--effort",
        help="the effort, for every stage: on Claude Code a level "
        f"({', '.join(EFFORTS)}), on OpenCode the model's variant (default: the "
        "profile's, else the one your host's settings give the model)",
    )
    review.add_argument("--budget", type=float, help=seating.BUDGET_HELP)
    review.add_argument("--rates", help=seating.RATES_HELP)
    review.add_argument(
        "--dry-run",
        action="store_true",
        help=DRY_RUN_HELP,
    )
    review.add_argument("--json", action="store_true", help=SUMMARY_JSON_HELP)
    prompts_argument(review)
    if not staged:
        no_search_argument(review)
        no_place_argument(review)
        no_circles_argument(review)
    output_argument(review)
    # Tuning, hidden from --help: most are profile values a flag overrides.
    review.add_argument(
        "--displays",
        choices=[rule.value for rule in DisplayRule],
        default=DisplayRule.IN_PARAGRAPH.value,
        help=argparse.SUPPRESS,
    )
    review.add_argument("--chunk", help=argparse.SUPPRESS)
    review.add_argument("--overlap", type=int, help=argparse.SUPPRESS)
    review.add_argument(
        "--outline", action=argparse.BooleanOptionalAction, help=argparse.SUPPRESS
    )
    for passes in (
        "--boundary-passes",
        "--role-passes",
        "--group-passes",
        "--edge-passes",
        "--cut-rounds",
        "--decide-passes",
    ):
        review.add_argument(passes, type=int, help=argparse.SUPPRESS)
    review.add_argument(
        "--rule-flags-in-edge-review",
        action=argparse.BooleanOptionalAction,
        help=argparse.SUPPRESS,
    )
    review.add_argument(
        "--judge", action=argparse.BooleanOptionalAction, help=argparse.SUPPRESS
    )
    review.add_argument(
        "--judge-host", choices=[h.value for h in Harness], help=argparse.SUPPRESS
    )
    review.add_argument("--judge-model", help=argparse.SUPPRESS)
    review.add_argument("--judge-rates", help=argparse.SUPPRESS)
    review.add_argument("--jobs", type=int, default=BUILD_JOBS, help=argparse.SUPPRESS)
    review.add_argument("--timeout", type=float, help=argparse.SUPPRESS)
    # a cut in the exchange format the later stages run on, in place of the
    # cutter's and the store's boundary answers
    review.add_argument("--cut", dest="base_cut", type=Path, help=argparse.SUPPRESS)
    if not staged:
        # develop mode: the store's answers folded again, keeping no state
        review.add_argument("--replay", action="store_true", help=argparse.SUPPRESS)


def no_search_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--no-search",
        action="store_true",
        help="skip the agent's search for support of the claims left standing "
        "unsupported, where the profile asks it (`support --ask` asks it later)",
    )


def no_place_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--no-place",
        action="store_true",
        help="skip the agent's placing of the lines of the blocks a line enters at "
        "a part, where the profile asks it (`place --ask` asks it later)",
    )


def no_circles_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--no-circles",
        action="store_true",
        help="skip the agent's question on the circles of what claims rest on, "
        "where the profile asks it (`circles --ask` asks it later)",
    )
