"""What an agent is asked after an edit, as a queue: the reread of the
owners of open stale marks, the search for a claim's support, the placing
of a block's lines at its parts, the question on a circle of what claims
rest on; each as its own command (`reread --ask`, `support`, `place`,
`circles`) and as the end of a build or an update, and each priced for a
dry run."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axiomation import (
    check,
    circles,
    edits,
    placing,
    queries,
    queue,
    rereads,
    support_search,
)
from axiomation.commands import (
    DRY_RUN_HELP,
    Paper,
    Say,
    UsageError,
    chosen,
    kept_arguments,
    kept_on,
    kept_store,
    paper_of,
    prompts_argument,
    requested,
    seating,
)
from axiomation.commands.seating import Asking, QueueSeats
from axiomation.driver import ReviewSettings
from axiomation.profiles import Agent, Profile
from axiomation.questions import Stage
from axiomation.questions import edge_sheet as es
from axiomation.records import RecordStore
from axiomation.runner import Runner, Stop
from axiomation.runner import given as queue_given
from axiomation.runner.queued import Price, QueueReport, tasks_left
from axiomation.versions import State


@dataclass(frozen=True)
class Step:
    """What an asking step printed, whether it ended whole (False only where
    its session is not answered), its dry run's estimate, and the claims
    its session would read that a later review may then be asked (the
    search's, each one it reads unargued a rule line)."""

    text: str
    whole: bool = True
    estimate: float = 0.0
    adds_subjects: int = 0
    #: a session touched a path outside its tools: the command's exit
    breached: bool = False


@dataclass(frozen=True, kw_only=True)
class Reread:
    """`reread PAPER [OWNER…] --ask`: the owners named (none: each owner of
    an open stale mark); `agent`, `person`, `session` and `reason` are a
    filed reread's, which `--ask` refuses."""

    paper: Paper
    owner: tuple[str, ...] = ()
    dry_run: bool = False
    prompts: Path | None = None
    agent: str | None = None
    person: str | None = None
    session: str | None = None
    reason: str | None = None
    asking: Asking = field(default_factory=Asking)


@dataclass(frozen=True, kw_only=True)
class Support:
    paper: Paper
    block: tuple[str, ...] = ()
    ask: bool = False
    dry_run: bool = False
    prompts: Path | None = None
    asking: Asking = field(default_factory=Asking)


@dataclass(frozen=True, kw_only=True)
class Place:
    paper: Paper
    block: tuple[str, ...] = ()
    ask: bool = False
    dry_run: bool = False
    prompts: Path | None = None
    asking: Asking = field(default_factory=Asking)


@dataclass(frozen=True, kw_only=True)
class Circles:
    paper: Paper
    circle: tuple[str, ...] = ()
    ask: bool = False
    dry_run: bool = False
    prompts: Path | None = None
    limit: int = queries.LIMIT
    json: bool = False
    asking: Asking = field(default_factory=Asking)


def request(arguments: argparse.Namespace) -> Reread | Support | Place | Circles:
    """The ask a command's parsed arguments give: `reread --ask`, `support`,
    `place` or `circles`."""
    kind = {"reread": Reread, "support": Support, "place": Place, "circles": Circles}
    return requested(
        kind[arguments.command],
        arguments,
        paper=paper_of(arguments),
        asking=seating.asking(arguments),
    )


def reread_asked(found: Reread, store: RecordStore, kept: State, say: Say) -> Step:
    """`reread PAPER [OWNER…] --ask`: the profile's edges agent takes each
    owner of an open mark (or those named) as a task, one at a time
    (`rereads`): each owner whose lines it says stand is filed with its
    answer as a reread; once every owner is settled, the lines it changed
    are given as its edges edit, a new state; then what it did to the
    gates. `--dry-run`: priced by what a task costs, nothing asked;
    `--prompts DIR`: the brief and the tasks under DIR. With the text,
    whether the step ended whole: False only where owners are still open."""
    if found.agent or found.person or found.session:
        raise UsageError(
            "--ask's author is the profile's edges agent: no --agent, "
            "--agent-session or --person"
        )
    if found.reason:
        raise UsageError("--ask takes no --reason: each owner's answer gives its own")
    given, defaulted = seating.default_profile(found.asking)
    profile = seating.profile_of(given)
    dry = found.dry_run
    asking = not dry and given.host != "none"
    if asking:
        given = seating.take_budget(
            given,
            found.paper.directory,
            found.paper.store,
            "reread --ask",
            seating.budget_needed(given, "reread --ask", defaulted),
            say,
        )
    agents = seating.seated(
        profile,
        ReviewSettings(stages=(Stage.EDGES,), given_cut=True),
        priced=True,
        hosted=asking,
    )
    seated = seating.edges_seated(agents[Stage.EDGES], asking or dry, "the reread")
    run = reread_run(store, kept, profile, given.profiles, seated, found.owner)
    try:
        with queue.opened(run) as rereading:
            assert isinstance(rereading, rereads.Rereading)
            asked = rereading.asked
            left = len(tasks_left(store, rereading))
            if found.prompts is not None and asked:
                write_queue_prompts(rereading, found.prompts)
    except rereads.RereadError as error:
        raise UsageError(str(error)) from None
    if not asked:
        return Step(f"state {kept.id} holds no open stale mark: nothing to reread")
    marked = sum(len(o.marks) for o in asked)
    lines = [
        f"reread --ask at state {kept.id}: {len(asked)} owners, {marked} marks; "
        f"run {run.key[:12]}"
    ]
    if dry:
        try:
            price = rereads.price(store, seated.model)
        except rereads.RereadError as error:
            raise UsageError(str(error)) from None
        cost, priced = _queue_priced(
            left, len(asked), "an owner", rereads.KIND, seated, price
        )
        lines[0] = "dry run, nothing asked or recorded: " + lines[0]
        lines += [
            *priced,
            *_prompts_line(found.prompts, "brief and tasks"),
            *(f"  {o.handle}: {', '.join(m.edge for m in o.marks)}" for o in asked),
        ]
        return Step("\n".join(lines), estimate=cost)
    view = queries.View(store, kept)
    before = check.verdict(view, found.paper.directory)
    hosts = seating.queue_hosts(seated) if asking else []
    runner = (
        seating.queue_runner(given, profile, store, seated, hosts) if asking else None
    )
    try:
        done = rereads.ask(run, seated.model, runner, hosts, seated.autocompact_tokens)
    except (edits.EditError, rereads.RereadError, queue.QueueError) as error:
        raise UsageError(str(error)) from None
    out = done.outcome
    breached = _queue_report(out.report, lines)
    if breached is not None:
        return breached
    for said in done.said:
        verdict = "stands" if said.stands else "changed"
        lines.append(f"{verdict}: {said.owner.handle} — {said.why}")
    # lines given by hand clear the marks they were shown only on the sheet
    return _queue_end(
        found.paper.directory,
        store,
        kept,
        view,
        before,
        out,
        lines,
        "owners",
        asking,
        "edit --stage edges",
    )


def support(found: Support, say: Say) -> Step:
    """`support PAPER [BLOCK…]`: the claims standing unsupported at a kept
    state, each with the search that read it unargued, where one is live.
    `--ask`: the profile's search steps take each one no live search read
    (or those of the blocks named) as a task, one at a time
    (`support_search`): once every claim is settled, the lines they found
    are given as the search's edges edit, and each claim the last step read
    unargued was filed with its answer as a search; then what it did to the
    gates. `--dry-run`: priced by what a task costs, nothing asked;
    `--prompts DIR`: each step's brief and the tasks under DIR. With the
    text, whether it ended whole: False only where claims are still open."""
    store = kept_store(found.paper)
    # what the queue settles is kept on top of the state it reads
    kept = (kept_on if found.ask else chosen)(found.paper, store)
    view = queries.View(store, kept)
    try:
        every = support_search.asked_claims(view, found.block, every=True)
    except support_search.SearchError as error:
        raise UsageError(str(error)) from None
    live = support_search.searched(store, kept, view.reader)
    if not found.ask:
        _refuse_dry_without_ask(found)
        lines = [
            f"{len(every)} claims standing unsupported at state {kept.id}, "
            f"{sum(support_search.claim_key(c.subject) not in live for c in every)} "
            "not searched"
        ]
        for c in every:
            named = f"{c.block}" + (f" [{c.handle}]" if c.handle else "")
            read = live.get(support_search.claim_key(c.subject))
            lines.append(
                f'  {named} "{c.words}"  '
                + (
                    "not searched"
                    if read is None
                    else f"searched by {read.author.name} at "
                    f"{read.record.state[:8]}, unargued: {read.record.reason}"
                )
            )
        return Step("\n".join(lines))
    given, defaulted = seating.default_profile(found.asking)
    profile = seating.profile_of(given)
    overridden = [f for f in ("model", "effort", "rates") if getattr(given, f, None)]
    if overridden and profile.support_search is not None:
        flags = ", ".join(f"--{f}" for f in overridden)
        raise UsageError(
            f"{flags}: the support search's steps are the profile's "
            f"[{profile.name}.support_search] table; name them there"
        )
    dry = found.dry_run
    asking = not dry and given.host != "none"
    if asking:
        given = seating.take_budget(
            given,
            found.paper.directory,
            found.paper.store,
            "support --ask",
            seating.budget_needed(given, "support --ask", defaulted),
            say,
        )
    seated = seating.search_seated(profile, hosted=asking)
    run = search_run(store, kept, profile, given.profiles, seated, found.block)
    with queue.opened(run) as search:
        assert isinstance(search, support_search.Searching)
        asked = search.asked
        left = len(tasks_left(store, search))
        if found.prompts is not None and asked:
            write_queue_prompts(search, found.prompts)
    if not asked:
        return Step(
            f"state {kept.id} holds no claim standing unsupported that no search "
            f"read ({len(every)} searched)"
        )
    lines = [
        f"support --ask at state {kept.id}: {len(asked)} claims; run {run.key[:12]}"
    ]
    if dry:
        cost, priced = _search_priced(store, seated, left)
        lines[0] = "dry run, nothing asked or recorded: " + lines[0]
        lines += [*priced, *_prompts_line(found.prompts, "briefs and tasks")]
        return Step("\n".join(lines), estimate=cost, adds_subjects=len(asked))
    before = check.verdict(view, found.paper.directory)
    hosts = seating.queue_hosts(seated) if asking else []
    runner = (
        seating.queue_runner(given, profile, store, seated, hosts) if asking else None
    )
    try:
        searched = support_search.ask(
            run,
            seated.model,
            runner,
            hosts,
            seated.shares,
            seated.autocompact_tokens,
        )
    except (edits.EditError, support_search.SearchError, queue.QueueError) as error:
        raise UsageError(str(error)) from None
    breached = _queue_report(searched.outcome.report, lines)
    if breached is not None:
        return breached
    for settled in searched.settled:
        c = settled.claim
        named = c.block + (f" [{c.handle}]" if c.handle else "")
        verdict = "argued" if settled.argued else "unargued"
        lines.append(f'{verdict}: {named} "{c.words}" — {settled.why}')
    return _queue_end(
        found.paper.directory,
        store,
        kept,
        view,
        before,
        searched.outcome,
        lines,
        "claims",
        asking,
    )


def circles_of(found: Circles, say: Say) -> Step:
    """`circles PAPER [CIRCLE…]`: the circles at a kept state as `open`
    lists them. `--ask`: the profile's edges agent takes each circle no word
    holds real (or those named) as a task, one at a time (`circles`), and
    names its lines' ends as the text relies on, or reads it real (filed);
    once every circle is settled, the lines narrowed are given as its edges
    edit; then what it did to the gates. `--dry-run`: priced by what a
    task costs, nothing asked; `--prompts DIR`: the brief and the tasks
    under DIR. With the text, whether it ended whole: False only where
    circles are still open."""
    store = kept_store(found.paper)
    # what the queue settles is kept on top of the state it reads
    kept = (kept_on if found.ask else chosen)(found.paper, store)
    view = queries.View(store, kept)
    if not found.ask:
        _refuse_dry_without_ask(found)
        items = queries.open_report(view, [queries.OpenKind.CIRCLE.value]).sections[
            queries.OpenKind.CIRCLE.section
        ]
        if found.circle:
            items = [
                i
                for i in items
                if any(i.data["cause"]["circle"].startswith(c) for c in found.circle)
            ]
        report = queries.Report(
            head=[f"{len(items)} circles at state {kept.id}"],
            data={"state": kept.id},
            sections={queries.OpenKind.CIRCLE.section: items},
        )
        return Step(queries.render(report, found.limit, found.json))
    given, defaulted = seating.default_profile(found.asking)
    profile = seating.profile_of(given)
    dry = found.dry_run
    asking = not dry and given.host != "none"
    if asking:
        given = seating.take_budget(
            given,
            found.paper.directory,
            found.paper.store,
            "circles --ask",
            seating.budget_needed(given, "circles --ask", defaulted),
            say,
        )
    agents = seating.seated(
        profile,
        ReviewSettings(stages=(Stage.EDGES,), given_cut=True),
        priced=True,
        hosted=asking,
    )
    seated = seating.edges_seated(
        agents[Stage.EDGES], asking or dry, "the circles question"
    )
    run = circles_run(store, kept, profile, given.profiles, seated, found.circle)
    try:
        with queue.opened(run) as circling:
            assert isinstance(circling, circles.Circling)
            asked = circling.asked
            left = len(tasks_left(store, circling))
            if found.prompts is not None and asked:
                write_queue_prompts(circling, found.prompts)
    except circles.CircleError as error:
        raise UsageError(str(error)) from None
    if not asked:
        real = sum(c.key in view.read_real for c in view.circles)
        return Step(
            f"state {kept.id} holds no circle to ask"
            + (f" ({real} read real)" if real else "")
        )
    lines = [
        f"circles --ask at state {kept.id}: {len(asked)} circles, "
        f"{sum(len(a.lines) for a in asked)} lines; run {run.key[:12]}"
    ]
    if dry:
        try:
            price = circles.price(store, seated.model)
        except circles.CircleError as error:
            raise UsageError(str(error)) from None
        cost, priced = _queue_priced(
            left, len(asked), "a circle", circles.KIND, seated, price
        )
        lines[0] = "dry run, nothing asked or recorded: " + lines[0]
        lines += [
            *priced,
            *_prompts_line(found.prompts, "brief and tasks"),
            *(f"  {a.text.splitlines()[0]}" for a in asked),
        ]
        return Step("\n".join(lines), estimate=cost)
    before = check.verdict(view, found.paper.directory)
    hosts = seating.queue_hosts(seated) if asking else []
    runner = (
        seating.queue_runner(given, profile, store, seated, hosts) if asking else None
    )
    try:
        done = circles.ask(run, seated.model, runner, hosts, seated.autocompact_tokens)
    except (edits.EditError, circles.CircleError, queue.QueueError) as error:
        raise UsageError(str(error)) from None
    out = done.outcome
    breached = _queue_report(out.report, lines)
    if breached is not None:
        return breached
    for said in done.said:
        if said.real:
            lines.append(
                f"real: circle {said.circle.key} — {said.why}; proposal: "
                f"{said.proposal}"
            )
        else:
            lines.append(f"refined: circle {said.circle.key} — {said.why}")
    return _queue_end(
        found.paper.directory, store, kept, view, before, out, lines, "circles", asking
    )


def place_of(found: Place, say: Say) -> Step:
    """`place PAPER [BLOCK…]`: the blocks to place at a kept state (a line
    enters each at a part, and its own lines name no part they leave
    from), each with the word that reads it whole, where one is filed.
    `--ask`: the profile's edges agent takes each no word reads whole (or
    those named) as a task, one at a time (`placing`), and names the parts
    each line leaves from and each part writes, or reads it whole (filed);
    once every block is settled, the lines placed are given as its edges
    edit; then what it did to the gates. `--dry-run`: priced by what a task
    costs, nothing asked; `--prompts DIR`: the brief and the tasks under
    DIR. With the text, whether it ended whole: False only where blocks
    are still open."""
    store = kept_store(found.paper)
    # what the queue settles is kept on top of the state it reads
    kept = (kept_on if found.ask else chosen)(found.paper, store)
    view = queries.View(store, kept)
    if not found.ask:
        _refuse_dry_without_ask(found)
        every = placing.listed(view)
        if found.block:
            named = set()
            for ref in found.block:
                try:
                    named.add(view.handle(view.resolve(ref)))
                except queries.QueryError as error:
                    raise UsageError(f"{ref}: {error}") from None
            every = [a for a in every if a.handle in named]
        whole = placing.read_whole(store)
        lines = [
            f"{len(every)} blocks to place at state {kept.id}, "
            f"{sum(a.key not in whole for a in every)} not read whole"
        ]
        for a in every:
            read = whole.get(a.key)
            entered = sorted(
                {
                    p
                    for x in a.entering
                    if (m := es.EDGE.match(x.text)) is not None
                    for p in (m.group("parts") or "").split(", ")
                    if p
                }
            )
            lines.append(
                f"  {a.handle}: entered at [{', '.join(entered)}], {len(a.lines)} "
                f"lines, {len(a.qualifiers)} qualifiers  "
                + (
                    "not read"
                    if read is None
                    else f"read whole by {read.author} at "
                    f"{read.record.state[:8]}: {read.record.reason}"
                )
            )
        return Step("\n".join(lines))
    given, defaulted = seating.default_profile(found.asking)
    profile = seating.profile_of(given)
    dry = found.dry_run
    asking = not dry and given.host != "none"
    if asking:
        given = seating.take_budget(
            given,
            found.paper.directory,
            found.paper.store,
            "place --ask",
            seating.budget_needed(given, "place --ask", defaulted),
            say,
        )
    agents = seating.seated(
        profile,
        ReviewSettings(stages=(Stage.EDGES,), given_cut=True),
        priced=True,
        hosted=asking,
    )
    seated = seating.edges_seated(agents[Stage.EDGES], asking or dry, "the placing")
    run = place_run(store, kept, profile, given.profiles, seated, found.block)
    try:
        with queue.opened(run) as placed:
            assert isinstance(placed, placing.Placing)
            asked = placed.asked
            left = len(tasks_left(store, placed))
            if found.prompts is not None and asked:
                write_queue_prompts(placed, found.prompts)
    except placing.PlaceError as error:
        raise UsageError(str(error)) from None
    if not asked:
        return Step(f"state {kept.id} holds no block to place")
    lines = [
        f"place --ask at state {kept.id}: {len(asked)} blocks, "
        f"{sum(len(a.lines) for a in asked)} lines; run {run.key[:12]}"
    ]
    if dry:
        try:
            price = placing.price(store, seated.model)
        except placing.PlaceError as error:
            raise UsageError(str(error)) from None
        cost, priced = _queue_priced(
            left, len(asked), "a block", placing.KIND, seated, price
        )
        lines[0] = "dry run, nothing asked or recorded: " + lines[0]
        lines += [
            *priced,
            *_prompts_line(found.prompts, "brief and tasks"),
            *(f"  {a.handle}: {len(a.lines)} lines" for a in asked),
        ]
        return Step("\n".join(lines), estimate=cost)
    before = check.verdict(view, found.paper.directory)
    hosts = seating.queue_hosts(seated) if asking else []
    runner = (
        seating.queue_runner(given, profile, store, seated, hosts) if asking else None
    )
    try:
        done = placing.ask(run, seated.model, runner, hosts, seated.autocompact_tokens)
    except (edits.EditError, placing.PlaceError, queue.QueueError) as error:
        raise UsageError(str(error)) from None
    out = done.outcome
    breached = _queue_report(out.report, lines)
    if breached is not None:
        return breached
    for said in done.said:
        verdict = "whole" if said.whole else "placed"
        lines.append(f"{verdict}: {said.block.handle} — {said.why}")
    return _queue_end(
        found.paper.directory, store, kept, view, before, out, lines, "blocks", asking
    )


def _refuse_dry_without_ask(found: Support | Place | Circles) -> None:
    """`--dry-run` and `--prompts` without `--ask`: nothing is asked, so
    nothing is priced or served."""
    for flag, given in (("--dry-run", found.dry_run), ("--prompts", found.prompts)):
        if given:
            raise UsageError(f"{flag} goes with --ask: without it nothing is asked")


def _prompts_line(prompts: Path | None, what: str) -> list[str]:
    """A dry run's line naming where `--prompts` wrote `what`; none
    without it."""
    return [] if prompts is None else [f"  {what} under {prompts}"]


def _search_priced(
    store: RecordStore, seated: QueueSeats, claims: int
) -> tuple[float, list[str]]:
    """The search's dry run price on `claims`: what each step is expected to
    cost, the first line the whole; the price and its lines."""
    kinds = (support_search.WORKER_KIND, support_search.SETTLER_KIND)
    each = []
    for kind, agent in zip(kinds, seated.agents, strict=False):
        try:
            price = support_search.price(store, kind, agent.model)
        except support_search.SearchError as error:
            raise UsageError(str(error)) from None
        each.append(
            (
                price.cost(claims),
                seating.priced_words(kind, agent.model, price, "a claim"),
            )
        )
    total = sum(cost for cost, _ in each)
    return total, [f"expected ${total:.2f}: {claims} claims", *(w for _, w in each)]


def _queue_priced(
    tasks: int,
    of: int,
    what: str,
    kind: str,
    seated: QueueSeats,
    price: Price,
) -> tuple[float, list[str]]:
    """A one-step queue's dry run price on the `tasks` left of its `of`
    (`queued.tasks_left`), each `what`: the price and its lines, the
    whole, then its step's."""
    total = price.cost(tasks)
    return total, [
        f"expected ${total:.2f}: {tasks} tasks"
        + (f", {of - tasks} settled by a run before" if tasks < of else ""),
        seating.priced_words(kind, seated.model, price, what),
    ]


def _queue_report(report: QueueReport | None, lines: list[str]) -> Step | None:
    """A queue run's report added to `lines`: what it spent and why it
    stopped; the step a breach ends, nothing filed or given."""
    if report is None:
        return None
    lines.append(f"spent ${report.spent:.2f}")
    if report.stopped is not None:
        lines.append(f"stopped: {report.stopped.value} {report.detail}".rstrip())
    if report.stopped is Stop.BREACH:
        lines.append("nothing filed or given: the same command asks again")
        return Step("\n".join(lines), whole=False, breached=True)
    return None


def _queue_end(
    directory: Path,
    store: RecordStore,
    kept: State,
    view: queries.View,
    before: Any,
    out: queue_given.Outcome,
    lines: list[str],
    what: str,
    asking: bool,
    by_hand: str = "change",
) -> Step:
    """A queue command's end: its tasks open, or its edit refused (the lines
    then given `by_hand`, the command that gives them), or the state its
    edit made; then what it did to the gates."""
    if not out.complete:
        lines.append(
            f"{out.open} {what} open: nothing given yet; "
            + (
                "the same command resumes"
                if asking
                else "not asked: --host none asks nothing"
            )
        )
        return Step("\n".join(lines), whole=False)
    if out.problems:
        listed = "; ".join(out.problems)
        raise UsageError(
            f"the lines found were refused: {listed}; the answers stand in the "
            "store, so the same command refuses them again: give the lines by "
            f"hand (`{by_hand}`)"
        )
    # a fresh view: the run's answers filed their judgments since
    after_view = queries.View(store, kept)
    if out.state is not None:
        assert out.edit is not None
        lines.append(f"edges edit {out.edit[:12]}: state {out.state.id}")
        after_view = queries.View(store, out.state)
    gates = check.delta(before, check.verdict(after_view, directory))
    lines += [line for i in gates for line in i.line.splitlines()] or [
        "gates: unchanged"
    ]
    return Step("\n".join(lines))


def write_queue_prompts(workflow: queue.Workflow, root: Path) -> None:
    """What a queue's agents are served, under `root`: each step's brief,
    and each task as the first step that takes it is first served it."""
    root.mkdir(parents=True, exist_ok=True)
    for step in workflow.steps:
        (root / f"{step.kind}.md").write_text(step.template.render(), encoding="utf-8")
    working = queue.Working(workflow)
    tasks = [
        f"task {t.id}\n{first.render(t, working)}"
        for t in workflow.draw(working)
        # a task as the first step that takes it is served it
        for first in [next(s for s in workflow.steps if queue.takes(s, t))]
    ]
    (root / "tasks.md").write_text("\n".join(tasks), encoding="utf-8")


def reread_after(
    store: RecordStore,
    kept: State,
    profile: Profile,
    profiles: str | Path | None,
    runner: Runner | None,
    agent: Agent,
) -> tuple[dict[str, Any], Stop | None] | None:
    """The reread an update ends with (`docs/model.md` § 5): a queue of the
    edges agent on the owners of the open marks of the state it kept, every
    version's, a gate mark's owners first, from what is left of the budget
    (`rereads.ask`; `runner` None: only what the store holds is replayed).
    Its counts, and why its run stopped; None where no mark is open."""
    if not queries.View(store, kept).marks:
        return None
    try:
        seated = seating.edges_seated(agent, runner is not None, "the reread")
        run = reread_run(store, kept, profile, profiles, seated)
        with queue.opened(run) as rereading:
            assert isinstance(rereading, rereads.Rereading)
            asked = rereading.asked
        hosts = seating.queue_hosts(seated) if runner is not None else []
        done = rereads.ask(
            run,
            seated.model,
            runner if hosts else None,
            hosts,
            seated.autocompact_tokens,
        )
    except (
        edits.EditError,
        rereads.RereadError,
        queue.QueueError,
        UsageError,
    ) as error:
        # the update's state is kept whatever its reread meets
        return {"refused": [str(error)]}, None
    out = done.outcome
    stopped = None if out.report is None else out.report.stopped
    counts: dict[str, Any] = {
        "owners": len(asked),
        "marks": sum(len(o.marks) for o in asked),
        "spent": round(0.0 if out.report is None else out.report.spent, 6),
        **({"stopped": stopped.value} if stopped is not None else {}),
        "stands": sum(s.stands for s in done.said),
        "changed": sum(not s.stands for s in done.said),
    }
    if not out.complete:
        return counts | {"unanswered": out.open}, stopped
    after = kept
    if out.problems:
        counts["refused"] = list(out.problems)
    elif out.state is not None:
        after = out.state
        counts["state"] = after.id
    left = queries.View(store, after)
    counts["open_marks"] = len(left.gate_marks)
    counts["open_descriptions"] = len(left.marks) - len(left.gate_marks)
    return counts, stopped


def circles_after(
    store: RecordStore,
    kept: State,
    profile: Profile,
    profiles: str | Path | None,
    runner: Runner | None,
    agent: Agent,
) -> tuple[dict[str, Any], Stop | None] | None:
    """The question a build or an update ends with (`docs/model.md` § 9): a
    queue of the edges agent on the circles of the state it kept that no
    word holds real, the heaviest first, from what is left of the budget
    (`circles.ask`; `runner` None: only what the store holds is replayed).
    Its counts, and why its run stopped; None where no circle is asked."""
    try:
        seated = seating.edges_seated(agent, runner is not None, "the circles question")
        run = circles_run(store, kept, profile, profiles, seated)
        with queue.opened(run) as circling:
            assert isinstance(circling, circles.Circling)
            asked = circling.asked
        if not asked:
            return None
        hosts = seating.queue_hosts(seated) if runner is not None else []
        done = circles.ask(
            run,
            seated.model,
            runner if hosts else None,
            hosts,
            seated.autocompact_tokens,
        )
    except (
        edits.EditError,
        circles.CircleError,
        queue.QueueError,
        UsageError,
    ) as error:
        # the run's state is kept whatever its question meets
        return {"refused": [str(error)]}, None
    out = done.outcome
    stopped = None if out.report is None else out.report.stopped
    counts: dict[str, Any] = {
        "circles": len(asked),
        "lines": sum(len(a.lines) for a in asked),
        "spent": round(0.0 if out.report is None else out.report.spent, 6),
        **({"stopped": stopped.value} if stopped is not None else {}),
        "refined": sum(not s.real for s in done.said),
        "real": sum(s.real for s in done.said),
    }
    if not out.complete:
        return counts | {"unanswered": out.open}, stopped
    after = kept
    if out.problems:
        counts["refused"] = list(out.problems)
    elif out.state is not None:
        after = out.state
        counts["state"] = after.id
    counts["open_circles"] = len(queries.View(store, after).circles)
    return counts, stopped


def place_after(
    store: RecordStore,
    kept: State,
    profile: Profile,
    profiles: str | Path | None,
    runner: Runner | None,
    agent: Agent,
) -> tuple[dict[str, Any], Stop | None] | None:
    """The placing a build or an update ends with (`docs/model.md` § 9): a
    queue of the edges agent on the blocks of the state it kept to place
    that no word reads whole, from what is left of the budget
    (`placing.ask`; `runner` None: only what the store holds is replayed).
    Its counts, and why its run stopped; None where no block is asked."""
    try:
        seated = seating.edges_seated(agent, runner is not None, "the placing")
        run = place_run(store, kept, profile, profiles, seated)
        with queue.opened(run) as placed:
            assert isinstance(placed, placing.Placing)
            asked = placed.asked
        if not asked:
            return None
        hosts = seating.queue_hosts(seated) if runner is not None else []
        done = placing.ask(
            run,
            seated.model,
            runner if hosts else None,
            hosts,
            seated.autocompact_tokens,
        )
    except (
        edits.EditError,
        placing.PlaceError,
        queue.QueueError,
        UsageError,
    ) as error:
        # the run's state is kept whatever its placing meets
        return {"refused": [str(error)]}, None
    out = done.outcome
    stopped = None if out.report is None else out.report.stopped
    counts: dict[str, Any] = {
        "blocks": len(asked),
        "lines": sum(len(a.lines) for a in asked),
        "spent": round(0.0 if out.report is None else out.report.spent, 6),
        **({"stopped": stopped.value} if stopped is not None else {}),
        "placed": sum(not s.whole for s in done.said),
        "whole": sum(s.whole for s in done.said),
    }
    if not out.complete:
        return counts | {"unanswered": out.open}, stopped
    if out.problems:
        counts["refused"] = list(out.problems)
    elif out.state is not None:
        counts["state"] = out.state.id
    return counts, stopped


def support_after(
    store: RecordStore,
    kept: State,
    profile: Profile,
    profiles: str | Path | None,
    runner: Runner | None,
) -> tuple[dict[str, Any], Stop | None] | None:
    """The search a build or an update ends with (`docs/model.md` § 8): the
    profile's search steps on the claims standing unsupported at the state
    it kept that no live search read, from what is left of the budget
    (`support_search.ask`; `runner` None: only what the store holds is
    replayed). Its counts, and why its run stopped; None where no claim is
    asked."""
    if not support_search.asked_claims(queries.View(store, kept)):
        return None
    try:
        seated = seating.search_seated(profile, hosted=runner is not None)
        run = search_run(store, kept, profile, profiles, seated)
        hosts = seating.queue_hosts(seated) if runner is not None else []
        found = support_search.ask(
            run,
            seated.model,
            runner if hosts else None,
            hosts,
            seated.shares,
            seated.autocompact_tokens,
        )
    except (
        edits.EditError,
        support_search.SearchError,
        queue.QueueError,
        UsageError,
    ) as error:
        # the run's state is kept whatever its search meets
        return {"refused": [str(error)]}, None
    report = found.outcome.report
    stopped = None if report is None else report.stopped
    counts: dict[str, Any] = {
        "claims": len(found.settled) + found.outcome.open,
        "spent": round(0.0 if report is None else report.spent, 6),
        **({"stopped": stopped.value} if stopped is not None else {}),
        "argued": sum(s.argued for s in found.settled),
        "unargued": sum(not s.argued for s in found.settled),
    }
    if not found.complete:
        return counts | {"unanswered": found.outcome.open}, stopped
    if found.outcome.problems:
        counts["refused"] = list(found.outcome.problems)
    elif found.outcome.state is not None:
        counts["state"] = found.outcome.state.id
    return counts, stopped


def search_run(
    store: RecordStore,
    kept: State,
    profile: Profile,
    profiles: str | Path | None,
    seated: QueueSeats,
    blocks: Sequence[str] = (),
) -> queue.Run:
    """The search's queue run on `kept`: the claims of `blocks` (none named:
    the claims of every block), its kept state opened under the profile,
    the settling step where the profile seats one, its edit
    `seated.model`'s."""
    return queue.Run(
        store,
        kept,
        support_search.FACTORY,
        {
            **seating.run_profile(profile, profiles),
            "blocks": list(blocks),
            "settle": len(seated.agents) > 1,
            "model": seated.model,
        },
    )


def reread_run(
    store: RecordStore,
    kept: State,
    profile: Profile,
    profiles: str | Path | None,
    seated: QueueSeats,
    owners: Sequence[str] = (),
) -> queue.Run:
    """The reread's queue run on `kept`: the owners named (none: every
    owner of an open mark), its kept state opened under the profile, its
    edit `seated.model`'s."""
    return queue.Run(
        store,
        kept,
        rereads.FACTORY,
        {
            **seating.run_profile(profile, profiles),
            "owners": list(owners),
            "model": seated.model,
        },
    )


def place_run(
    store: RecordStore,
    kept: State,
    profile: Profile,
    profiles: str | Path | None,
    seated: QueueSeats,
    named: Sequence[str] = (),
) -> queue.Run:
    """The placing's queue run on `kept`: the blocks named (none: each to
    place that no word reads whole), its kept state opened under the
    profile, its edit `seated.model`'s."""
    return queue.Run(
        store,
        kept,
        placing.FACTORY,
        {
            **seating.run_profile(profile, profiles),
            "blocks": list(named),
            "model": seated.model,
        },
    )


def circles_run(
    store: RecordStore,
    kept: State,
    profile: Profile,
    profiles: str | Path | None,
    seated: QueueSeats,
    named: Sequence[str] = (),
) -> queue.Run:
    """The circles' queue run on `kept`: the circles named by their key's
    prefix (none: each no word holds real), its kept state opened under
    the profile, its edit `seated.model`'s."""
    return queue.Run(
        store,
        kept,
        circles.FACTORY,
        {
            **seating.run_profile(profile, profiles),
            "circles": list(named),
            "model": seated.model,
        },
    )


def reread_priced(
    store: RecordStore, started: State | None, seated: QueueSeats | None
) -> dict[str, Any]:
    """An update dry run's `reread_if_it_fires`, where the update ends with
    the reread: its price (`rereads.price`) on each owner of an open mark
    at the state it starts from, one at least, since the update's own marks
    are not yet known. None, with `reread_not_priced`, where it
    starts from no state."""
    if seated is None:
        return {}
    if started is None:
        return {
            "reread_if_it_fires": None,
            "reread_not_priced": "the update starts from no kept state",
        }
    owners = len(rereads.owners(queries.View(store, started)))
    try:
        price = rereads.price(store, seated.model)
    except rereads.RereadError as error:
        raise UsageError(str(error)) from None
    return {"reread_if_it_fires": round(price.cost(max(owners, 1)), 4)}


def support_priced(
    store: RecordStore, started: State | None, seated: QueueSeats | None
) -> dict[str, Any]:
    """An update dry run's `support_if_it_fires`, where the update ends with
    the search for its claims' support: the search's price
    (`seating.search_cost`) on each claim the state it starts from holds
    standing unsupported that no live search read; the update's own claims
    are not yet known. None, with `support_not_priced`, where it starts
    from no state."""
    if seated is None:
        return {}
    if started is None:
        return {
            "support_if_it_fires": None,
            "support_not_priced": "the update starts from no kept state",
        }
    claims = len(support_search.asked_claims(queries.View(store, started)))
    return {"support_if_it_fires": round(seating.search_cost(store, seated, claims), 4)}


def place_priced(
    store: RecordStore, started: State | None, seated: QueueSeats | None
) -> dict[str, Any]:
    """An update dry run's `place_if_it_fires`, where the update ends with
    the placing: its price (`placing.price`) on each block to place at the
    state it starts from that no word reads whole, one at least. None,
    with `place_not_priced`, where it starts from no state."""
    if seated is None:
        return {}
    if started is None:
        return {
            "place_if_it_fires": None,
            "place_not_priced": "the update starts from no kept state",
        }
    whole = placing.read_whole(store)
    try:
        listed = placing.listed(queries.View(store, started))
    except edits.EditError as error:
        # a state whose edges do not read back takes no edit, nor a placing
        return {"place_if_it_fires": None, "place_not_priced": str(error)}
    asked = [a for a in listed if a.key not in whole]
    try:
        price = placing.price(store, seated.model)
    except placing.PlaceError as error:
        raise UsageError(str(error)) from None
    return {"place_if_it_fires": round(price.cost(max(len(asked), 1)), 4)}


def circles_priced(
    store: RecordStore, started: State | None, seated: QueueSeats | None
) -> dict[str, Any]:
    """An update dry run's `circles_if_it_fires`, where the update ends with
    the circles' question: their price (`circles.price`) on each circle at
    the state it starts from that no word holds real, one at least. None,
    with `circles_not_priced`, where it starts from no state."""
    if seated is None:
        return {}
    if started is None:
        return {
            "circles_if_it_fires": None,
            "circles_not_priced": "the update starts from no kept state",
        }
    view = queries.View(store, started)
    asked = [c for c in view.circles if c.key not in view.read_real]
    try:
        price = circles.price(store, seated.model)
    except circles.CircleError as error:
        raise UsageError(str(error)) from None
    return {"circles_if_it_fires": round(price.cost(max(len(asked), 1)), 4)}


def _circles_arguments(commands: Any) -> None:
    found = commands.add_parser(
        "circles",
        help="the circles of what claims rest on at a kept state, each once, the "
        "most claims held back first; with --ask, an agent names which way the "
        "text relies along each, or finds the circle is the text's own",
    )
    kept_arguments(found)
    found.add_argument(
        "circle",
        nargs="*",
        help="a circle by a prefix of its key (`axiomation open --kind circle` "
        "lists them); default: every circle, with --ask every one nobody has "
        "found to be the text's own",
    )
    found.add_argument(
        "--ask",
        action="store_true",
        help="ask the profile's edges agent what each end on the circles relies "
        "on: the ends it names are given as its edit of the edges, a new state; "
        "a circle it finds is the text's own is filed with its reason and "
        "proposal",
    )
    found.add_argument(
        "--dry-run", action="store_true", help=f"with --ask: {DRY_RUN_HELP}"
    )
    prompts_argument(found, with_ask=True)
    found.add_argument(
        "--limit",
        type=int,
        default=queries.LIMIT,
        help=f"circles listed (default {queries.LIMIT}; 0: all)",
    )
    found.add_argument("--json", action="store_true", help="JSON, not lines")
    seating.asking_arguments(found, "the circles' session", "the question")


def _place_arguments(commands: Any) -> None:
    found = commands.add_parser(
        "place",
        help="the blocks a line enters at a part whose own lines name no part they "
        "leave from, at a kept state; with --ask, an agent names the part each "
        "line leaves from and the parts each part writes",
    )
    kept_arguments(found)
    found.add_argument(
        "block",
        nargs="*",
        help="these blocks (a handle, a node id or a handle's prefix); default: "
        "every block to place, with --ask every one nobody has read whole",
    )
    found.add_argument(
        "--ask",
        action="store_true",
        help="ask the profile's edges agent where each line of each block leaves "
        "from: the lines it places are given as its edit of the edges, a new "
        "state; a block it reads whole is filed with its reason",
    )
    found.add_argument(
        "--dry-run", action="store_true", help=f"with --ask: {DRY_RUN_HELP}"
    )
    prompts_argument(found, with_ask=True)
    seating.asking_arguments(found, "the placing's session", "the placing")


def _support_arguments(commands: Any) -> None:
    support = commands.add_parser(
        "support",
        help="the claims standing unsupported at a kept state, each with whether "
        "a search found no support for it; with --ask, an agent searches the "
        "paper for the argument of each claim no search in force has read: the "
        "lines it finds are given as its edit of the edges, the claims it finds "
        "unargued are filed",
    )
    kept_arguments(support)
    support.add_argument(
        "block",
        nargs="*",
        help="the claims of these blocks (a handle, a node id or a handle's "
        "prefix); default: every claim standing unsupported",
    )
    support.add_argument(
        "--ask",
        action="store_true",
        help="ask the profile's edges agent where the paper argues each claim no "
        "search in force has read: the lines it finds are given as its edit of "
        "the edges, a new state; a claim it finds unargued is filed as its "
        "search",
    )
    support.add_argument(
        "--dry-run", action="store_true", help=f"with --ask: {DRY_RUN_HELP}"
    )
    prompts_argument(support, with_ask=True)
    seating.asking_arguments(support, "the search's session", "the search")


#: The asks' own commands' arguments, by command; `reread`'s are a
#: filing's (`commands.filings`).
ARGUMENTS: dict[str, Callable[[Any], None]] = {
    "circles": _circles_arguments,
    "place": _place_arguments,
    "support": _support_arguments,
}
