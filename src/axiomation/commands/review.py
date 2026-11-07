"""`review`: the review sessions on a kept state, each priced, asked of
the profile's reviewers, its sheet filed as the agent's and its lines
given as the agent's edits; its queues (citation, fieldresults' sourcing,
methods); a kept answer filed again under today's check; or the open
findings as a referee report."""

from __future__ import annotations

import argparse
import functools
import shutil
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import astuple, dataclass, field, replace
from pathlib import Path
from typing import Any

from axiomation import (
    check,
    corpus,
    edits,
    evidence,
    methods_queue,
    queries,
    queue,
    review,
    support_search,
    versions,
)
from axiomation.commands import (
    DRY_RUN_HELP,
    Exit,
    Paper,
    Ran,
    Say,
    UsageError,
    asks,
    chosen,
    kept_arguments,
    kept_on,
    kept_store,
    paper_of,
    prompts_argument,
    requested,
    scratch_copy,
    seating,
)
from axiomation.commands.seating import Asking
from axiomation.driver import ReviewSettings
from axiomation.evidence import work_of
from axiomation.profiles import (
    Agent,
    Profile,
    ProfileError,
    read_profiles,
    review_settings,
)
from axiomation.questions import Stage
from axiomation.questions.review_file import (
    COLUMNS,
    CONVENTIONS_FILE,
    POINTERS,
    Session,
    SessionGroup,
    named_alone,
    sessions_named,
)
from axiomation.records import AnswerRecord, Aspect, RecordStore
from axiomation.resolver import Resolver
from axiomation.run_state import RunFile, made_from, text_of
from axiomation.runner import LikeRuns, Runner, Stop, like_runs
from axiomation.runner.hosts import estimate
from axiomation.runner.queued import run_queue
from axiomation.versions import State

#: Scoped review sessions asked at once.
REVIEW_JOBS = 4


@dataclass(frozen=True, kw_only=True)
class Review:
    """`review PAPER`: the sessions named (none: each seat the profile asks
    by default), what they ask (`all_again`, the `scope`s, `by_section`), a dry run,
    the directory each session's files are written under (`prompts`), a
    kept answer to file again, or the report; the profile,
    the budget and `host` none in `asking`; `observed`: the cited works
    filed already, on a dry run's scratch copy."""

    paper: Paper
    session: tuple[str, ...] = ()
    all_again: bool = False
    scope: tuple[str, ...] = ()
    by_section: bool = False
    report: bool = False
    dry_run: bool = False
    prompts: Path | None = None
    file_answer: str | None = None
    limit: int = queries.LIMIT
    json: bool = False
    observed: bool = False
    asking: Asking = field(default_factory=Asking)


def request(arguments: argparse.Namespace) -> Review:
    """`review`'s request from its parsed arguments."""
    return requested(
        Review, arguments, paper=paper_of(arguments), asking=seating.asking(arguments)
    )


def run(reviewed: Review, say: Say) -> Ran:
    """`review PAPER [--session S…] [--all-again] [--report] [--dry-run]
    [--prompts DIR] [--budget USD]`: each session's brief on the kept state, priced;
    asked, its sheet filed as the agent's, its edge lines given as the
    agent's edges edit; or the open findings as a referee report."""
    if reviewed.file_answer is not None:
        return Ran(_file_answer(reviewed, say))
    store = kept_store(reviewed.paper)
    # what a session gives is kept on top of the state it reads
    kept = (chosen if reviewed.report else kept_on)(reviewed.paper, store)
    if reviewed.report:
        report = queries.review_report(queries.View(store, kept))
        say.write(queries.render(report, reviewed.limit, reviewed.json))
        return Ran(0)
    profile = _profile(reviewed.asking)
    seated = list(profile.reviewers)
    sessions = sessions_named(reviewed.session, seated) or [
        s for s in seated if profile.reviewers[s].asked_by_default
    ]
    if seated and not sessions:
        raise UsageError(
            f"profile {profile.name} asks no session by default "
            "(`asked_by_default`): name one with --session, or --session all"
        )
    left = [s.value for s in seated if s not in sessions]
    if left and not reviewed.session:
        say.line(
            f"not asked by default: {', '.join(left)} (--session NAME asks one, "
            "--session all every seated session)"
        )
    # a session named by itself is refused where the profile seats none or
    # it has nothing to read; a group's member is skipped, with a line
    named = named_alone(reviewed.session)
    unseated = [s.value for s in named if s not in profile.reviewers]
    if unseated:
        raise UsageError(
            f"profile {profile.name} seats no reviewer for "
            f"{', '.join(unseated)} (`review.<session>`)"
        )
    skipped = [s.value for s in sessions if s not in profile.reviewers]
    if skipped and len(skipped) < len(sessions):
        say.line(
            f"not seated: {', '.join(skipped)} (profile {profile.name} has no "
            "`review.<session>` for it); not asked"
        )
    sessions = [s for s in sessions if s in profile.reviewers]
    if not sessions:
        raise UsageError(
            f"profile {profile.name} seats no reviewer for "
            f"{', '.join(skipped) or 'any session'} (`review.<session>`)"
        )
    scopes = [[n for n in given.split(",") if n] for given in reviewed.scope]
    if reviewed.by_section and scopes:
        raise UsageError("--by-section takes no --scope")
    if (scopes or reviewed.by_section) and Session.FIELDRESULTS in sessions:
        # refused or left out before any session is asked and paid for
        whole = (
            "the fieldresults session's inventory reads the whole paper and its "
            "queue sources each result once: it takes no --scope or --by-section"
        )
        if Session.FIELDRESULTS in named:
            raise UsageError(whole)
        say.line(f"fieldresults: {whole}; not asked")
        sessions.remove(Session.FIELDRESULTS)
    select = review.Selection.ALL if reviewed.all_again else review.Selection.OPEN
    asking = not reviewed.dry_run and reviewed.asking.host != "none"
    if asking:
        reviewed = replace(
            reviewed,
            asking=seating.take_budget(
                reviewed.asking,
                reviewed.paper.directory,
                reviewed.paper.store,
                "review",
                "asking a model needs --budget (--dry-run prices it first)",
                say,
            ),
        )
    directory = Path(reviewed.paper.directory)
    conventions_path = directory / CONVENTIONS_FILE
    conventions = (
        conventions_path.read_text(encoding="utf-8")
        if conventions_path.is_file()
        else None
    )
    try:
        sources = evidence.sources_for(
            directory, RecordStore(reviewed.paper.store).run_directory
        )
    except evidence.EvidenceError as error:
        say.note(
            f"axiomation review: note: no cited page is read, and the cited works "
            f"are not compared with the store: {error}"
        )
        sources = None
    if sources is not None and not reviewed.observed:
        # the corpus as it is now, whichever command or edit changed it,
        # before the review decides what to ask (docs/model.md § 8)
        observed = corpus.observe(
            store, kept, sources, corpus.rested_keys(store.judgment_records())
        )
        for line in observed.lines():
            say.line(f"cited works: {line}")
        if observed.news and reviewed.dry_run:
            with scratch_copy(reviewed.paper) as paper:
                corpus.file(RecordStore(paper.store), observed, corpus.OBSERVER)
                return run(replace(reviewed, paper=paper, observed=True), say)
        corpus.file(store, observed, corpus.OBSERVER)
    spent = 0.0
    code = 0
    like = like_runs(store.answers())
    round_priced = (0.0, 0.0)
    # the sessions' sheets, held until their edge and group lines are
    # given, so a resumed round reads them from its run (docs/architecture.md § 4)
    held = RunFile(store.run_directory, "review", kept.id, made_from(profile))
    with edits.opened(store, kept, review_settings(profile)) as opened:
        for session in sessions:
            reviewer = profile.reviewers[session]
            if (
                session in (Session.CITATIONCHECK, Session.FIELDRESULTS)
                and sources is None
            ):
                # its tasks are read on the cited works' pages alone; the
                # fieldresults inventory serves a queue that reads them
                if session in named:
                    raise UsageError(
                        f"the {session.value} session reads the cited works, and "
                        "none can be read here: `axiomation sources` sets them up"
                    )
                say.line(f"{session.value}: no cited work can be read here; not asked")
                continue
            if session is Session.METHODS:
                try:
                    cost, asked = _methods_queue(
                        reviewed,
                        store,
                        kept,
                        profile,
                        select,
                        scopes,
                        asking,
                        reviewed.asking.budget - spent if asking else 0.0,
                        say,
                    )
                except review.NoSubject as error:
                    if session in named:
                        raise
                    say.line(f"{session.value}: {error}; not asked")
                    continue
                if reviewed.dry_run:
                    round_priced = tuple(a + cost for a in round_priced)
                else:
                    spent += cost
                code = code or asked
                continue
            if session is Session.CITATIONCHECK:
                assert sources is not None
                try:
                    cost, asked = _citation_queue(
                        reviewed,
                        store,
                        kept,
                        profile,
                        select,
                        scopes,
                        sources,
                        asking,
                        reviewed.asking.budget - spent if asking else 0.0,
                        say,
                    )
                except review.NoSubject as error:
                    if session in named:
                        raise
                    say.line(f"{session.value}: {error}; not asked")
                    continue
                if reviewed.dry_run:
                    round_priced = tuple(a + cost for a in round_priced)
                else:
                    spent += cost
                code = code or asked
                continue
            agent = seating.reviewer_agent(reviewer.agent)
            assert agent.rates is not None
            # the same command at the same state is the same round: asked
            # again, its answered sessions replay
            round_id = review.review_round(
                kept.id,
                session,
                reviewer.template,
                select,
                scopes,
                reviewed.by_section,
                forager=reviewer.forager_model,
                reasks=review.round_answers(
                    queries.View(opened.store, opened.state), session
                ),
            )
            if scopes or reviewed.by_section:
                make = functools.partial(
                    review.scoped_briefs,
                    scopes=scopes,
                    sections=reviewed.by_section,
                    forager=reviewer.forager_model,
                    round_id=round_id,
                )
            else:
                make = functools.partial(
                    _one_brief,
                    forager=reviewer.forager_model,
                    round_id=round_id,
                )
            try:
                scoped = make(
                    opened,
                    session,
                    reviewer.template,
                    sources,
                    conventions,
                    select,
                    directory,
                )
            except review.NoSubject as error:
                if session in named:
                    raise
                # a seated session the paper gives nothing to read: asked by
                # name alone
                say.line(f"{session.value}: {error}; not asked")
                continue
            briefs = _fitted(scoped.briefs, agent)
            priced = _say_briefs(
                session,
                queries.View(opened.store, opened.state),
                briefs,
                agent,
                like,
                say,
                scoped.unasked,
                bool(scopes or reviewed.by_section),
                select,
            )
            if Aspect.TRUE in COLUMNS[session].judged:
                # a rule line is asked only of a claim a search read unargued
                unsearched = support_search.asked_claims(
                    queries.View(opened.store, opened.state)
                )
                if unsearched:
                    say.line(
                        f"  {len(unsearched)} claims standing unsupported that no "
                        "search read take no rule line: `axiomation support PAPER "
                        "--ask` searches them first"
                    )
            if reviewed.prompts is not None:
                for found in briefs:
                    review.written(reviewed.prompts, found)
            if reviewed.dry_run:
                round_priced = tuple(
                    a + b for a, b in zip(round_priced, priced, strict=True)
                )
                if session is Session.FIELDRESULTS:
                    expected = _fieldresults_queue_priced(
                        store,
                        profile,
                        briefs,
                        None
                        if reviewed.prompts is None
                        else reviewed.prompts / Session.FIELDRESULTS,
                        say,
                    )
                    round_priced = tuple(a + expected for a in round_priced)
                continue
            by_key = {found.question.key: found for found in briefs}
            todo = [k for k in by_key if k not in store.authoritative()]
            if todo and asking:
                executable = shutil.which(seating.EXECUTABLES[agent.harness])
                if executable is None:
                    raise UsageError(
                        f"{agent.harness.value} needs "
                        f"`{seating.EXECUTABLES[agent.harness]}` on PATH"
                    )
                runner = Runner(
                    seating.host_of(agent, executable),
                    Resolver(store, run=held),
                    reviewed.asking.budget - spent,
                    agent.rates,
                    jobs=REVIEW_JOBS if len(briefs) > 1 else 1,
                    timeout=profile.session_timeout_seconds,
                    transcripts=store.transcripts,
                    profile=profile.name,
                    expect=reviewed.asking.expect,
                    review_round=round_id,
                    review_state=kept.id,
                )
                checks = {
                    key: review.checker(
                        opened, found, sources, edits.agent(agent.model, None)
                    )
                    for key, found in by_key.items()
                }
                ran = runner.run(
                    [by_key[k].question for k in todo],
                    lambda question, text, checks=checks: checks[question.key](
                        question, text
                    ),
                )
                spent += ran.spent
                say.line(f"  asked: ${ran.spent:.2f} spent, {ran.attempts} attempts")
                if ran.stopped is not None:
                    say.line(f"  stopped: {ran.stopped.value}: {ran.detail}")
                    code = Exit.BREACH if ran.stopped is Stop.BREACH else Exit.STOPPED
            authoritative = store.authoritative()
            answered = [
                (found, authoritative[key])
                for key, found in by_key.items()
                if key in authoritative
            ]
            if len(answered) < len(by_key):
                say.line(
                    "  not answered"
                    + (
                        f": {len(by_key) - len(answered)} of {len(by_key)}"
                        if len(by_key) > 1
                        else ""
                    )
                    + ("" if asking else " (nothing asked)")
                )
                code = code or Exit.UNANSWERED
            if not answered:
                continue
            # the run is removed only once its round's lines were given: an
            # answer it no longer holds was given by the run that asked it
            given_before = all(
                held.text(a.key) is None and not a.raw for _, a in answered
            )
            texts = (
                {}
                if given_before
                else {a.key: text_of(a, held, store.run_directory) for _, a in answered}
            )
            given = _filed_and_given(
                store,
                opened,
                answered,
                texts,
                len(answered) == len(by_key) and not given_before,
                reviewed,
                say,
            )
            code = code or given
            if given_before:
                continue
            if session is Session.FIELDRESULTS:
                # its inventory answered whole, its results are sourced by
                # its queue (`sources.fieldresults_queue`)
                if len(answered) == len(by_key):
                    cost, asked = _fieldresults_queue(
                        reviewed,
                        store,
                        kept,
                        profile,
                        select,
                        _inventory_results(answered, texts),
                        round_id,
                        asking,
                        reviewed.asking.budget - spent if asking else 0.0,
                        say,
                    )
                    spent += cost
                    code = code or asked
                continue
            if len(answered) == len(by_key):
                grouped, cost = group_lines_given(
                    store,
                    answered,
                    texts,
                    profile,
                    reviewed,
                    reviewed.asking.budget - spent if asking else None,
                    kept.id,
                    say,
                )
                spent += cost
                code = code or grouped
    if not code and not reviewed.dry_run:
        held.kept()
    return Ran(code, round_priced)


def _profile(given: Asking) -> Profile:
    """The profile the review asks under, by name (none: the default): a
    review takes none of the flags `seating.profile_of` lays over one."""
    try:
        profiles = read_profiles(given.profiles)
    except ProfileError as error:
        raise UsageError(str(error)) from None
    name = given.profile or seating.DEFAULT_PROFILE
    if name not in profiles:
        raise UsageError(
            f"no profile {name!r}; there are {', '.join(sorted(profiles))}"
        )
    return profiles[name]


def _one_brief(*given: Any, **named: Any) -> review.Scoped:
    return review.Scoped([review.brief(*given, **named)])


#: The most characters of a scope a dry run prints: an update's unread
#: blocks, by handle, may run to hundreds.
SCOPE_SHOWN = 300


def _laid_words(files: Collection[str]) -> str:
    """The cited works laid in, as the dry run counts them: works, and
    their files where a work holds several. No page is served whole until
    a session reads it, so their size prices nothing."""
    works = {work_of(k) for k in files}
    more = f" ({len(files)} files)" if len(files) != len(works) else ""
    return f"{len(works)} cited works laid in{more}"


def _unread_words(key: str, read: evidence.Results) -> str:
    """A cited work the session could not read, and why, in the read's
    own words."""
    return f"  {key} not read: {read.detail or read.read_status.value}"


def _bibliography_words(spec: Mapping[str, Any]) -> str:
    """The works of the bibliography a session's tools read, laid in or
    not, as the dry run counts them; nothing where it reads none."""
    entries = spec.get("entries") or ()
    return (
        f", its tools read the bibliography's {len(entries)} works" if entries else ""
    )


def _shortened(scope: str) -> str:
    return scope if len(scope) <= SCOPE_SHOWN else scope[: SCOPE_SHOWN - 1] + "…"


#: What `--session` names: a session, or a group of them.
SESSION_CHOICES = [s.value for s in Session] + [g.value for g in SessionGroup]

#: `--session`'s help on `review` and `sync`: what each session reads.
SESSION_HELP = (
    "a session to ask, or a group (repeat; default: those the profile asks "
    "by default): argument (is each claim true, does each proof establish it), "
    "citationcheck (does each cited work say what the paper cites it for), "
    "methods (does each script compute what the text prints), findings "
    "(problems a reader of the whole paper meets), contribution (is the result "
    "known), fieldresults (does each result of the field the paper uses carry "
    "its source), relatedwork (is the paper placed in its field); field: the "
    "last three, all: every session the profile seats"
)


def _say_briefs(
    session: Session,
    view: queries.View,
    briefs: Sequence[review.Brief],
    agent: Agent,
    like: LikeRuns,
    say: Say,
    unasked: Sequence[review.Unasked] = (),
    scoped: bool = False,
    select: review.Selection = review.Selection.OPEN,
) -> tuple[float, float]:
    """Each session's brief priced, a scoped draw's sessions together
    (`scoped`), with what the default held back, its subjects no reading
    was ever filed on (`review.first_reads`), what a session that judges
    nothing reads, and each scope with nothing to ask; the
    estimate, its turns fitted (`_fitted`), and the expected cost: that
    times what like runs cost in the store, as the runner's check reads
    it."""
    assert agent.rates is not None
    floors = [
        estimate(
            f.question.prompt,
            agent.rates,
            f.question.workspace,
            seating.harness_tokens(agent),
        )
        + f.question.turns_estimate
        for f in briefs
    ]
    expected = sum(
        like.expected(f.question.template.name, floor, fitted=True)
        for floor, f in zip(floors, briefs, strict=True)
    )
    works = {k: n for f in briefs for k, n in f.works.items()}
    unread = {k: why for f in briefs for k, why in f.unread.items()}
    state = view.state.id
    if briefs:
        # the factor is the session's, every brief's template the same
        words = seating.expected_words(
            like, briefs[0].question.template.name, sum(floors), fitted=True
        )
        say.line(f"{session.value}: {words}")
    if len(briefs) == 1 and not scoped:
        found = briefs[0]
        say.line(
            f"{session.value} at state {state}: asks {len(found.subjects)} subjects, "
            + (
                f"{len(found.questions)} findings to settle, "
                if found.questions
                else ""
            )
            + f"{_laid_words(works)}"
            + (f", {len(unread)} unread" if unread else "")
            + _bibliography_words(found.spec)
            + f"; estimate ${floors[0]:.2f} (the session and its subjects; a "
            "sheet of a few subjects costs less); "
            f"key {found.question.key[:12]}"
        )
        if found.subjects:
            say.line(f"  its subjects: {review.kinds_words(found.subjects.values())}")
            first = review.first_reads(view, found)
            if first:
                say.line(f"  {review.first_words(first, len(found.subjects))}")
        if found.held:
            say.line(f"  {review.held_words(found.held)}")
        if not COLUMNS[session].judges:
            say.line(
                f"  {review.text_words(found, session, select)}"
                + (f" ({_shortened(found.scope)})" if found.scope else "")
            )
    else:
        say.line(
            f"{session.value} at state {state}, one session per scope: "
            f"{len(briefs)} sessions, {_laid_words(works)} across them"
            + (f", {len(unread)} unread" if unread else "")
            + f"; estimate ${sum(floors):.2f} together "
            f"(${min(floors):.2f}–${max(floors):.2f} a session, each with its "
            "subjects; a sheet of a few subjects costs less)"
        )
        held = [h for f in briefs for h in f.held] + [
            h for u in unasked for h in u.held
        ]
        if COLUMNS[session].judges and held:
            asked = sum(len(f.subjects) for f in briefs)
            say.line(f"  in all: asks {asked} subjects; {review.held_words(held)}")
        every = [s for f in briefs for s in f.subjects.values()]
        if every:
            say.line(f"  its subjects, in all: {review.kinds_words(every)}")
            first = sum(review.first_reads(view, f) for f in briefs)
            if first:
                say.line(f"  {review.first_words(first, len(every))}")
        for found, floor in zip(briefs, floors, strict=True):
            # a session that judges nothing reads its scope's text, and
            # settles its findings there
            read = (
                f"asks {len(found.subjects)} subjects"
                + (
                    f", holds back {review.held_count(found.held)}"
                    if found.held
                    else ""
                )
                if COLUMNS[session].judges
                else review.text_words(found)
            )
            say.line(
                f"  {_shortened(found.scope or '')}: {read}, "
                f"{len(found.works)} works laid in, estimate ${floor:.2f}"
            )
        for u in unasked:
            say.line(
                f"  {u.scope}: not asked, {review.held_words(u.held)}"
                if u.held
                else f"  {u.scope}: not asked: nothing to ask"
            )
    for key, why in unread.items():
        say.line(_unread_words(key, why))
    for why in dict.fromkeys(why for f in briefs for why in f.unplaced):
        say.line(f"  a citation no subject holds: {why}")
    if COLUMNS[session].edges:
        say.line(
            "  asked, it may also correct a line of the graph it read, a new state, "
            "or make a group, whose edges are then asked on the profile's agents "
            "(a re-ask, at a cost of its own)"
        )
    return sum(floors), expected


def _fitted(briefs: Sequence[review.Brief], agent: Agent) -> list[review.Brief]:
    """Each brief's question with what its turns cost at the agent's rates
    (`Brief.turns_cost`), so the runner's estimate is the session's fitted
    price."""
    assert agent.rates is not None
    return [
        replace(
            f, question=replace(f.question, turns_estimate=f.turns_cost(agent.rates))
        )
        for f in briefs
    ]


def _citation_queue(
    reviewed: Review,
    store: RecordStore,
    kept: State,
    profile: Profile,
    select: review.Selection,
    scopes: Sequence[Sequence[str]],
    sources: evidence.Sources,
    asking: bool,
    budget: float,
    say: Say,
) -> tuple[float, int]:
    """The citationcheck session as a queue (`sources.citation_queue`), in its review
    round: priced and, with `--prompts`, its briefs and tasks written; asked
    of the profile's citation seats within `budget`, what it settled filed.
    Returns what it spent (a dry run: what it is expected to cost) and its
    exit code."""
    from axiomation.sources import citation_queue

    if scopes or reviewed.by_section:
        raise UsageError(
            "the citationcheck session is a queue, each session a share of its sites: "
            "it takes no --scope or --by-section"
        )
    reviewer = profile.reviewers[Session.CITATIONCHECK]
    finder = reviewer.finder
    seated = seating.queue_seated(
        [
            *([(finder.agent, finder.budget_share)] if finder else []),
            (reviewer.agent, reviewer.budget_share),
        ],
        None,
        asking or reviewed.dry_run,
        "the citationcheck session",
        f"seat [{profile.name}.review.citationcheck] on claude-code",
    )
    view = queries.View(store, kept)
    round_id = review.review_round(
        kept.id,
        Session.CITATIONCHECK,
        reviewer.template,
        select,
        page_finder=finder.agent.model if finder else None,
        reasks=review.round_answers(view, Session.CITATIONCHECK),
    )
    run = queue.Run(
        store,
        kept,
        citation_queue.FACTORY,
        {
            **seating.run_profile(profile, reviewed.asking.profiles),
            "paper": str(Path(reviewed.paper.directory).resolve()),
            "select": select.value,
            "round": round_id,
            "find": finder is not None,
            "model": seated.model,
        },
    )
    try:
        with queue.opened(run) as workflow:
            assert isinstance(workflow, citation_queue.Citing)
            found = workflow.found
            if reviewed.prompts is not None:
                # under the session's name, as every review session's files
                asks.write_queue_prompts(
                    workflow, reviewed.prompts / Session.CITATIONCHECK
                )
    except citation_queue.CitationError as error:
        raise UsageError(str(error)) from None
    sites, settles = len(found.subjects), len(found.questions)
    kinds = [citation_queue.FINDER_KIND] if finder else []
    kinds.append(citation_queue.READER_KIND)
    each = []
    for kind, agent in zip(kinds, seated.agents, strict=True):
        try:
            price = citation_queue.price(store, kind, agent.model, finder is not None)
        except citation_queue.CitationError as error:
            raise UsageError(str(error)) from None
        tasks = sites if kind == citation_queue.FINDER_KIND else sites + settles
        each.append(
            (
                price.cost(tasks, citation_queue.sessions(tasks)),
                seating.priced_words(kind, agent.model, price, "a task"),
            )
        )
    cost = sum(c for c, _ in each)
    say.line(
        f"{Session.CITATIONCHECK} at state {kept.id}: {sites} sites"
        + (f", {settles} findings to settle" if settles else "")
        + f", {len(found.works) + len(found.unread)} cited works"
        + (f" ({len(found.unread)} unread)" if found.unread else "")
        + f"; a queue, run {run.key[:12]}; expected ${cost:.2f}"
    )
    first = review.first_reads(view, found)
    if first:
        say.line(f"  {review.first_words(first, sites)}")
    for _, line in each:
        say.line(f"  {line}")
    if reviewed.dry_run:
        return cost, 0
    if not asking:
        say.line("  not answered (nothing asked)")
        return 0.0, Exit.UNANSWERED
    return _review_queue_asked(
        reviewed, profile, store, seated, budget, round_id, run, say
    )


def _fieldresults_seated(profile: Profile, hosted: bool) -> seating.QueueSeats:
    """The fieldresults queue's steps: its finder, then its reader, the
    session's own agent."""
    reviewer = profile.reviewers[Session.FIELDRESULTS]
    finder = reviewer.finder
    assert finder is not None  # the profile refuses a fieldresults seat without
    return seating.queue_seated(
        [(finder.agent, finder.budget_share), (reviewer.agent, reviewer.budget_share)],
        None,
        hosted,
        "the fieldresults session",
        f"seat [{profile.name}.review.fieldresults] on claude-code",
    )


def _fieldresults_queue_priced(
    store: RecordStore,
    profile: Profile,
    briefs: Sequence[review.Brief],
    root: Path | None,
    say: Say,
) -> float:
    """What the fieldresults queue is expected to cost where its inventory
    is not yet answered (a dry run): its tasks are the inventory's lines,
    reckoned here one a claim standing `unsupported`, an upper bound (an
    inventory lists somewhat fewer results to source than such claims),
    each step's price a result said; its steps' briefs written under
    `root`, where one is given. Returns the expected cost."""
    from axiomation.sources import fieldresults_queue as fq

    seated = _fieldresults_seated(profile, True)
    results = max(len(b.spec.get(POINTERS) or ()) for b in briefs)
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
    expected = 0.0
    for kind, agent, brief in zip(
        (fq.FINDER_KIND, fq.READER_KIND),
        seated.agents,
        (fq.FINDER_BRIEF, fq.READER_BRIEF),
        strict=True,
    ):
        if root is not None:
            (root / f"{kind}.md").write_text(brief, encoding="utf-8")
        try:
            price = fq.price(store, kind, agent.model)
        except fq.FieldResultsError as error:
            raise UsageError(str(error)) from None
        expected += price.cost(results, fq.sessions(results))
        then = seating.priced_words(kind, agent.model, price, "a result")
        say.line(f"  then {then.strip()}")
    say.line(
        f"  its queue: about {results} results (one a claim standing unsupported), "
        f"expected ${expected:.2f}"
    )
    return expected


def _inventory_results(
    answered: Sequence[tuple[review.Brief, AnswerRecord]], texts: dict[str, str]
) -> list[Any]:
    """The results the inventories `answered` list to source, each once
    (inventories of one round may list one twice), in their order."""
    from axiomation.sources import fieldresults_queue as fq

    return list(
        dict.fromkeys(i for f, a in answered for i in fq.items(f, texts[a.key]))
    )


def _fieldresults_queue(
    reviewed: Review,
    store: RecordStore,
    kept: State,
    profile: Profile,
    select: review.Selection,
    results: Sequence[Any],
    round_id: str,
    asking: bool,
    budget: float,
    say: Say,
) -> tuple[float, int]:
    """The fieldresults session's queue (`sources.fieldresults_queue`), on
    the `results` its answered inventories list to source, in its review
    round: priced, asked of the profile's fieldresults seats within
    `budget`, what it settled filed. Returns what it spent and its exit
    code."""
    from axiomation.sources import fieldresults_queue as fq

    seated = _fieldresults_seated(profile, asking)
    run = queue.Run(
        store,
        kept,
        fq.FACTORY,
        {
            **seating.run_profile(profile, reviewed.asking.profiles),
            "paper": str(Path(reviewed.paper.directory).resolve()),
            "select": select.value,
            "round": round_id,
            "results": [list(astuple(r)) for r in results],
            "model": seated.model,
        },
    )
    try:
        with queue.opened(run) as workflow:
            assert isinstance(workflow, fq.Fielding)
            results = len(workflow.listed)
            if reviewed.prompts is not None and results:
                asks.write_queue_prompts(
                    workflow, reviewed.prompts / Session.FIELDRESULTS
                )
        each = []
        for kind, agent in zip(
            (fq.FINDER_KIND, fq.READER_KIND), seated.agents, strict=True
        ):
            price = fq.price(store, kind, agent.model)
            each.append(
                (
                    price.cost(results, fq.sessions(results)),
                    seating.priced_words(kind, agent.model, price, "a result"),
                )
            )
    except fq.FieldResultsError as error:
        raise UsageError(str(error)) from None
    cost = sum(c for c, _ in each)
    say.line(
        f"  its queue: {results} results to source; run {run.key[:12]}; "
        f"expected ${cost:.2f}"
    )
    for _, line in each:
        say.line(f"    {line}")
    if not results:
        return 0.0, 0
    if not asking:
        say.line("  not answered (nothing asked)")
        return 0.0, Exit.UNANSWERED
    return _review_queue_asked(
        reviewed,
        profile,
        store,
        seated,
        budget,
        round_id,
        run,
        say,
        (fq.FINDER_KIND, fq.READER_KIND),
    )


def _methods_queue(
    reviewed: Review,
    store: RecordStore,
    kept: State,
    profile: Profile,
    select: review.Selection,
    scopes: Sequence[Sequence[str]],
    asking: bool,
    budget: float,
    say: Say,
) -> tuple[float, int]:
    """The methods session as a queue (`methods_queue`), in its review
    round: priced and, with `--prompts`, its brief and tasks written; asked
    of the profile's methods seat within `budget`, what it settled filed.
    Returns what it spent (a dry run: what it is expected to cost) and its
    exit code."""
    if scopes or reviewed.by_section:
        raise UsageError(
            "the methods session is a queue, each session a share of its pairs: "
            "it takes no --scope or --by-section"
        )
    reviewer = profile.reviewers[Session.METHODS]
    seated = seating.queue_seated(
        [(reviewer.agent, reviewer.budget_share)],
        None,
        asking or reviewed.dry_run,
        "the methods session",
        f"seat [{profile.name}.review.methods] on claude-code",
    )
    round_id = review.review_round(
        kept.id,
        Session.METHODS,
        reviewer.template,
        select,
        reasks=review.round_answers(queries.View(store, kept), Session.METHODS),
    )
    run = queue.Run(
        store,
        kept,
        methods_queue.FACTORY,
        {
            **seating.run_profile(profile, reviewed.asking.profiles),
            "paper": str(Path(reviewed.paper.directory).resolve()),
            "select": select.value,
            "round": round_id,
            "model": seated.model,
        },
    )
    try:
        with queue.opened(run) as workflow:
            assert isinstance(workflow, methods_queue.Methods)
            found = workflow.found
            if reviewed.prompts is not None:
                asks.write_queue_prompts(workflow, reviewed.prompts / Session.METHODS)
        price = methods_queue.price(store, seated.model)
    except methods_queue.MethodsError as error:
        raise UsageError(str(error)) from None
    pairs, settles = len(found.subjects), len(found.questions)
    cost = price.cost(pairs + settles)
    say.line(
        f"methods at state {kept.id}: {pairs} "
        + ("pair" if pairs == 1 else "pairs")
        + " of a block and a script"
        + (f", {settles} findings to settle" if settles else "")
        + f"; a queue, run {run.key[:12]}; expected ${cost:.2f}"
    )
    words = seating.priced_words(
        methods_queue.READER_KIND, seated.model, price, "a pair"
    )
    say.line(f"  {words}")
    if reviewed.dry_run:
        return cost, 0
    if not asking:
        say.line("  not answered (nothing asked)")
        return 0.0, Exit.UNANSWERED
    return _review_queue_asked(
        reviewed, profile, store, seated, budget, round_id, run, say
    )


def _review_queue_asked(
    reviewed: Review,
    profile: Profile,
    store: RecordStore,
    seated: seating.QueueSeats,
    budget: float,
    round_id: str,
    run: queue.Run,
    say: Say,
    kinds: Collection[str] | None = None,
) -> tuple[float, int]:
    """A review session's queue asked of its seats within `budget`, in its
    review round, what it settled filed and said: of its answers of
    `kinds` where given (the fieldresults inventory, in the same round, is
    said already). Returns what it spent and its exit code."""
    hosts = seating.queue_hosts(seated)
    runner = seating.queue_runner(
        reviewed.asking, profile, store, seated, hosts, budget, round_id
    )
    report = run_queue(runner, run, hosts, seated.shares)
    held = store.judgment_records()
    mine = {
        r
        for a in store.authoritative().values()
        if a.review_round == round_id and (kinds is None or a.kind in kinds)
        for r in a.records
    }
    say.line(
        f"  asked: ${report.spent:.2f} spent; {report.settled} tasks settled, "
        f"{report.open} open; filed in the round: "
        f"{sum(1 for j in held.judgments if j.id in mine)} judgments, "
        f"{sum(1 for f in held.findings if f.id in mine)} findings"
    )
    if report.stopped is not None:
        say.line(f"  stopped: {report.stopped.value}: {report.detail}")
        return report.spent, (
            Exit.BREACH if report.stopped is Stop.BREACH else Exit.STOPPED
        )
    return report.spent, Exit.UNANSWERED if report.open else 0


def _filed_and_given(
    store: RecordStore,
    opened: edits.Kept,
    answered: Sequence[tuple[review.Brief, AnswerRecord]],
    texts: dict[str, str],
    whole: bool,
    reviewed: Review,
    say: Say,
) -> int:
    """What the sessions' answers filed, said, then their edge lines given
    as their agent's edges edit, where the round is `whole` (each of its
    sessions answered); nonzero where the lines could not be given."""
    held = store.judgment_records()
    mine = {r for _, a in answered for r in a.records}
    first = answered[0][1]
    say.line(
        f"  filed by {first.host} {first.model} ("
        + (
            f"answer {first.key[:12]}"
            if len(answered) == 1
            else f"{len(answered)} answers"
        )
        + f"): {sum(1 for j in held.judgments if j.id in mine)} judgments, "
        f"{sum(1 for f in held.findings if f.id in mine)} findings"
        + (
            f", {sum(1 for r in held.rulings if r.id in mine)} rulings"
            if any(r.id in mine for r in held.rulings)
            else ""
        )
    )
    author = _review_author(answered)
    if not whole:
        # the edge lines wait for every subject's answer, given once
        return 0
    try:
        gave = review.edges_given(opened, answered, texts, author)
    except edits.EditError as error:
        say.line(f"  edge lines: {error}")
        # the lines stay in the answers' sheets, given by none
        return 0 if "holds this edit already" in str(error) else 1
    for why in gave.left_out:
        say.line(f"  edge lines left out, an earlier subject's given: {why}")
    given = gave.given
    if given is None:
        return 0
    if given.problems:
        listed = "; ".join(given.problems)
        say.line(f"  edge lines refused, nothing recorded: {listed}")
        return 1
    if given.state is not None:
        say.line(f"  edge lines given: state {given.state.id}")
        for line in _edited(store, opened.state, given.state, reviewed):
            say.line(f"    {line}")
    return 0


def _review_author(
    answered: Sequence[tuple[review.Brief, AnswerRecord]],
) -> edits.Author:
    """The agent the sessions' answers are filed as, who gives their lines."""
    first = answered[0][1]
    return edits.agent(
        first.model,
        f"{first.kind} {first.key[:12]}"
        + (f" and {len(answered) - 1} more" if len(answered) > 1 else ""),
    )


def group_lines_given(
    store: RecordStore,
    answered: Sequence[tuple[review.Brief, AnswerRecord]],
    texts: dict[str, str],
    profile: Profile | None,
    reviewed: Review,
    budget: float | None,
    read_at: str,
    say: Say,
) -> tuple[int, float]:
    """The sessions' group lines given as their agent's groups edit of the
    latest state, its edge lines' where they made one, the edges the edit
    reopens asked on the profile's roles and edges agents within `budget`;
    with no budget or profile they are not given, since an edit left
    pending would hold the version, and the command that gives them is
    printed (`--state read_at`, the state the sessions read, which replays
    their answers). The exit code, nonzero where they were refused or
    wait, and what the re-ask spent."""
    if not any(review.answer_sheet(f, texts[a.key]).groups for f, a in answered):
        return 0, 0.0
    # the state the review read, as `kept_on` gave it
    kept = versions.latest(store, waiting=True)
    assert kept is not None
    again = (
        f"`axiomation review {reviewed.paper.directory} --state {read_at} --session "
        f"{answered[0][0].session.value} --budget USD`"
    )
    with edits.opened(
        store, kept, None if profile is None else review_settings(profile)
    ) as now:
        try:
            if (
                review.groups_sheet(
                    now, *(review.answer_sheet(f, texts[a.key]) for f, a in answered)
                )
                is None
            ):
                return 0, 0.0  # each group the state holds already
        except edits.EditError as error:
            say.line(f"  group lines refused, nothing recorded: {error}")
            return 1, 0.0
        if profile is None or budget is None or budget <= 0:
            say.line(
                "  group lines not given: a groups edit asks its edges again, on the "
                f"profile's agents; {again} gives them"
            )
            return Exit.UNANSWERED, 0.0
        agents = seating.seated(
            profile,
            ReviewSettings(stages=(Stage.ROLES, Stage.EDGES), given_cut=True),
            priced=True,
            hosted=True,
        )
        seats = seating.seats_of(agents, seating.executables_of(agents))
        runner = Runner(
            seats[Stage.EDGES].host,
            Resolver(store),
            budget,
            seats[Stage.EDGES].rates,
            timeout=profile.session_timeout_seconds,
            transcripts=store.transcripts,
            seats=seats,
            profile=profile.name,
            expect=reviewed.asking.expect,
        )
        try:
            gave = review.groups_given(
                now, answered, texts, _review_author(answered), runner
            )
        except edits.EditError as error:
            say.line(f"  group lines: {error}")
            return 1, 0.0
    for why in gave.left_out:
        say.line(f"  group lines left out, an earlier subject's given: {why}")
    given = gave.given
    if given is None:
        return 0, 0.0
    spent = sum(r.spent for r in given.reports)
    if given.problems:
        say.line(
            f"  group lines refused, nothing recorded: {'; '.join(given.problems)}"
        )
        return 1, spent
    if given.state is None:
        asked = ", ".join(f"{q.key[:12]} ({q.stage.value})" for q in given.unanswered)
        say.line(
            f"  group lines given as a groups edit, waiting for what it reopens "
            f"({asked}, ${spent:.2f} spent): {again} resumes it"
        )
        return Exit.UNANSWERED, spent
    say.line(
        f"  group lines given: state {given.state.id} (${spent:.2f} for its re-ask)"
    )
    for line in _edited(store, kept, given.state, reviewed):
        say.line(f"    {line}")
    return 0, spent


def _file_answer(reviewed: Review, say: Say) -> int:
    """`review PAPER --file-answer KEY`: a kept review answer the runner's
    check refused, checked again under today's check at the paper's
    current kept state; filed, its edge lines given, where it passes;
    its problems listed, nothing written, where it does not. Asks no
    model."""
    for flag, given in (
        ("--session", reviewed.session),
        ("--scope", reviewed.scope),
        ("--by-section", reviewed.by_section),
        ("--all-again", reviewed.all_again),
        ("--report", reviewed.report),
        ("--dry-run", reviewed.dry_run),
        ("--prompts", reviewed.prompts),
    ):
        if given:
            raise UsageError(
                f"--file-answer takes no {flag}: the answer's own session and "
                "subjects are asked again"
            )
    store = kept_store(reviewed.paper)
    kept = kept_on(reviewed.paper, store)
    latest = versions.latest(store, waiting=True)
    if latest is None or kept.id != latest.id:
        raise UsageError(
            f"--file-answer files onto the paper's current kept state "
            f"({latest.id if latest else 'none'}), not {kept.id}"
        )
    profile = _profile(reviewed.asking)
    directory = Path(reviewed.paper.directory)
    conventions_path = directory / CONVENTIONS_FILE
    conventions = (
        conventions_path.read_text(encoding="utf-8")
        if conventions_path.is_file()
        else None
    )
    try:
        sources = evidence.sources_for(
            directory, RecordStore(reviewed.paper.store).run_directory
        )
    except evidence.EvidenceError as error:
        say.note(f"axiomation review: note: no cited page is read: {error}")
        sources = None
    answer = review.kept_answer(store, reviewed.file_answer)
    session = review.answer_session(answer)
    reviewer = profile.reviewers.get(session)
    if reviewer is None:
        raise UsageError(
            f"profile {profile.name} seats no reviewer for {session.value} "
            f"(`review.{session.value}`)"
        )
    with edits.opened(store, kept, review_settings(profile)) as opened:
        found = review.answer_brief(
            opened,
            answer,
            reviewer.template,
            sources,
            conventions,
            directory,
            reviewer.forager_model,
        )
        check = review.checker(opened, found, sources, edits.agent(answer.model, None))
        validation = check(found.question, answer.raw)
        say.line(
            f"{session.value} answer {answer.key[:12]} (attempt {answer.attempt}) at "
            f"state {kept.id}, checked again: {len(found.subjects)} subjects, "
            f"{len(found.questions)} findings to settle"
        )
        if not validation.valid:
            say.line(f"  refused, nothing filed ({len(validation.problems)} problems):")
            for problem in validation.problems:
                say.line(f"  - {problem}")
            return 1
        resolver = Resolver(store)
        with resolver.claim():
            if answer.key in store.authoritative():
                raise UsageError(f"answer {answer.key[:12]} was filed meanwhile")
            recorded = resolver.checked_again(answer, validation)
        filed = [(found, recorded.answer)]
        texts = {answer.key: answer.raw}
        code = _filed_and_given(store, opened, filed, texts, True, reviewed, say)
    # its group lines ask a re-ask, which --file-answer never does: it
    # prints the command that gives them
    grouped, _ = group_lines_given(
        store, filed, texts, None, reviewed, None, kept.id, say
    )
    return code or grouped


def _edited(store: RecordStore, old: State, new: State, reviewed: Review) -> list[str]:
    """What an edges edit did, as lines: the readings it lapsed and its gate
    delta (`docs/model.md` § 8), each line of an item on its own."""
    before, after = queries.View(store, old), queries.View(store, new)
    lapsed = queries.lapsed_between(before, after)
    gates = check.delta(
        check.verdict(before, reviewed.paper.directory),
        check.verdict(after, reviewed.paper.directory),
    )
    return [
        f"lapsed ({len(lapsed)})",
        *(line for i in lapsed for line in i.line.splitlines()),
        *(line for i in gates for line in i.line.splitlines()),
    ]


def _review_command(commands: Any) -> None:
    """`review`: the review sessions on a kept state."""
    command = commands.add_parser(
        "review",
        help="have the review agents read a kept state, each session over the "
        "whole paper, and file their judgments and findings",
        # no prefix stands for a flag: `--all` would read as `--all-again`
        allow_abbrev=False,
    )
    kept_arguments(command)
    command.add_argument(
        "--session",
        action="append",
        default=[],
        choices=SESSION_CHOICES,
        metavar="SESSION",
        help=SESSION_HELP,
    )
    command.add_argument(
        "--all-again",
        action="store_true",
        help="ask every subject in scope again, a reading that holds too, and "
        "read the whole text for findings (default: only what a new reading "
        "can settle; the dry run counts what this adds)",
    )
    command.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="NAMES",
        help="one session on the subjects these names select, comma-separated: "
        "sections (3, 3.2, A1, or words of a title), lines "
        "(paper.md:120-240) or handles (repeat: a session each)",
    )
    command.add_argument(
        "--by-section",
        action="store_true",
        help="one session per top-level section, each on its subjects (a session "
        "that judges none, on its text), the paper whole at hand",
    )
    command.add_argument(
        "--report",
        action="store_true",
        help="print the open findings as a referee report, under each result; "
        "asks nothing",
    )
    command.add_argument(
        "--profile", help=f"the profile (default: {seating.DEFAULT_PROFILE})"
    )
    command.add_argument("--profiles", type=Path, help="a profiles file of your own")
    command.add_argument(
        "--budget",
        type=float,
        help="dollars the sessions may spend together (default: the one "
        "`axiomation settings` shows)",
    )
    command.add_argument("--dry-run", action="store_true", help=DRY_RUN_HELP)
    prompts_argument(command)
    command.add_argument(
        "--host",
        choices=["none"],
        help="none: ask no model, file what the store already answered (free)",
    )
    # re-checks a kept answer under today's check: a contributor's tool
    command.add_argument("--file-answer", metavar="KEY", help=argparse.SUPPRESS)
    command.add_argument(
        "--limit",
        type=int,
        default=queries.LIMIT,
        help=f"items a section shows (default {queries.LIMIT}; 0: all)",
    )
    command.add_argument("--json", action="store_true", help="JSON, not lines")


#: `review`'s arguments.
ARGUMENTS: dict[str, Callable[[Any], None]] = {"review": _review_command}
