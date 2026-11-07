"""`sync`: the author's round, the machine's part of it: the update where
the paper's text is ahead of the latest kept state, the asks an update
ends with, the review, then the inbox; each step its own command's run on
one budget."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from axiomation import versions
from axiomation.commands import (
    DRY_RUN_HELP,
    STORE_HELP,
    Exit,
    Paper,
    Say,
    UsageError,
    asks,
    kept_on,
    kept_store,
    paper_arguments,
    paper_of,
    prompts_argument,
    reads,
    requested,
    seating,
)
from axiomation.commands import review as reviewing
from axiomation.commands import update as updating
from axiomation.commands.build import no_circles_argument, no_search_argument
from axiomation.commands.seating import Asking
from axiomation.questions.review_file import named_alone, sessions_named
from axiomation.records import RecordStore
from axiomation.versions import State


@dataclass(frozen=True, kw_only=True)
class Sync:
    """`sync PAPER`: the paper and its store, the root file, the review
    sessions asked (none: each seat the profile asks by default), a dry
    run, the directory each step's prompts are written under (`prompts`),
    the end steps left out, and the profile and budget the round's steps
    share (`asking`)."""

    paper: Paper
    root: Path | None = None
    session: tuple[str, ...] = ()
    dry_run: bool = False
    prompts: Path | None = None
    no_reread: bool = False
    no_search: bool = False
    no_circles: bool = False
    asking: Asking = field(default_factory=Asking)


def request(arguments: argparse.Namespace) -> Sync:
    """`sync`'s request from its parsed arguments."""
    return requested(
        Sync, arguments, paper=paper_of(arguments), asking=seating.asking(arguments)
    )


def run(asked: Sync, say: Say) -> int:
    """`sync PAPER`: `update` where the paper's text is not the latest kept
    state's version; then, as the profile asks them after an update,
    `reread --ask` on the marks still open, `support --ask` on the claims
    standing unsupported no live search read and `circles --ask` on the
    circles still unread, so a round the update left unanswered is asked
    again; then `review` over the sessions named, else each seat the
    profile asks by default, all on one budget; then the inbox at the
    latest state. Each step is its own command's run, with its flags'
    defaults; the round stops at the first step that does not end whole,
    and the same command resumes, each step replaying what the store
    answered. What it spent is read from the answers it added."""
    store = kept_store(asked.paper)
    # refused where joined copies kept states apart, before any step pays
    latest = kept_on(replace(asked.paper, state=None), store)
    # a session named alone that the profile seats no reviewer for, or a
    # group with no seated member, refused before any step pays, as the
    # review would refuse it after them
    seats = seating.profile_of(seating.default_profile(asked.asking)[0]).reviewers
    named = sessions_named(asked.session, list(seats))
    unseated = [s.value for s in named_alone(asked.session) if s not in seats]
    if not unseated and asked.session and not any(s in seats for s in named):
        unseated = [s.value for s in named] or ["any session"]
    if unseated:
        raise UsageError(
            f"the profile seats no reviewer for {', '.join(unseated)} "
            "(`review.<session>`): nothing asked"
        )
    dry = asked.dry_run
    given = asked.asking
    if not dry:
        given = seating.take_budget(
            given,
            asked.paper.directory,
            asked.paper.store,
            "sync",
            "asking a model needs --budget (--dry-run prices the round first)",
            say,
        )
    before = len(store.answers())
    paper = replace(asked.paper, state=None)

    def spent() -> float:
        return sum(a.cost for a in RecordStore(paper.store).answers()[before:])

    def left() -> float:
        assert given.budget is not None
        return max(given.budget - spent(), 0.0)

    # each step asks under the round's profile, with its own flags' defaults
    step_asking = Asking(
        profile=given.profile, profiles=given.profiles, expect=given.expect
    )
    priced = (0.0, 0.0)
    #: what the round's price leaves out, each in words
    partial: list[str] = []
    ahead = reads.files_version(store, paper.directory, latest) != latest.version
    if ahead:
        partial.append(
            f"the review is priced at state {latest.id}, without what the update "
            "and its search add"
        )
        say.line(f"sync: update, the paper's text ahead of state {latest.id}")
        update = updating.Update(
            paper=paper,
            root=asked.root,
            no_reread=asked.no_reread,
            no_search=asked.no_search,
            no_circles=asked.no_circles,
            dry_run=dry,
            prompts=_under(asked.prompts, "update"),
            asking=step_asking if dry else replace(step_asking, budget=given.budget),
        )
        ran = updating.run(update, say)
        code = ran.code
        if dry:
            # none where the update keeps the new files alone, asking nothing
            priced = priced if ran.priced is None else ran.priced
        elif code != 0:
            say.note(
                f"sync: stopped after the update, ${spent():.2f} spent: the same "
                "command resumes"
            )
            return code
    else:
        say.line(f"sync: the paper's text is state {latest.id}'s: no update")
    profile = seating.profile_of(seating.default_profile(step_asking)[0])
    for command, wanted in (
        ("reread", profile.reread_marks_after_update and not asked.no_reread),
        ("support", profile.search_support_after_edges and not asked.no_search),
        ("circles", profile.ask_circles_after_edges and not asked.no_circles),
    ):
        if not wanted:
            continue
        if dry and ahead:
            say.line(
                f"sync: {command} --ask: priced in the update's dry run, if it fires"
            )
            continue
        say.line(f"sync: {command} --ask")
        now = RecordStore(paper.store)
        prompts = _under(asked.prompts, command)
        asking = step_asking if dry else replace(step_asking, budget=left())
        if command == "reread":
            reread = asks.Reread(
                paper=paper, dry_run=dry, prompts=prompts, asking=asking
            )
            step = asks.reread_asked(reread, now, _latest(now), say)
        elif command == "support":
            searched = asks.Support(
                paper=paper, ask=True, dry_run=dry, prompts=prompts, asking=asking
            )
            step = asks.support(searched, say)
        else:
            circled = asks.Circles(
                paper=paper, ask=True, dry_run=dry, prompts=prompts, asking=asking
            )
            step = asks.circles_of(circled, say)
        say.line(step.text)
        # each queue's estimate is what its tasks are expected to cost
        priced = (priced[0] + step.estimate, priced[1] + step.estimate)
        if step.adds_subjects:
            partial.append(
                f"the search reads {step.adds_subjects} claims, and each it reads "
                "unargued adds a rule line to the review, priced before it"
            )
        if not step.whole:
            say.note(
                f"sync: stopped at {command} --ask, ${spent():.2f} spent: the same "
                "command resumes"
            )
            return Exit.UNANSWERED
    say.line("sync: review")
    reviewed = reviewing.Review(
        paper=paper,
        session=asked.session,
        dry_run=dry,
        prompts=asked.prompts,
        asking=replace(step_asking, budget=None if dry else left()),
    )
    if dry and ahead:
        say.line(
            f"  priced at state {latest.id}: what the update will reopen is "
            "not counted; `sync --dry-run` again once it is kept prices it"
        )
    ran = reviewing.run(reviewed, say)
    code = ran.code
    if dry:
        estimated, expected = (
            a + b for a, b in zip(priced, ran.priced or (0.0, 0.0), strict=True)
        )
        say.line(
            f"sync: the round, expected ${expected:.2f} (each step's expected as its "
            f"dry run says it), estimate ${estimated:.2f}"
            + (
                "; partial: " + "; ".join(partial) + ": `sync --dry-run` again once "
                "it has run prices them"
                if partial
                else ""
            )
        )
        return code
    assert given.budget is not None
    say.line(f"sync: ${spent():.2f} spent of ${given.budget:g}")
    if code != 0:
        say.note("sync: stopped in the review: the same command resumes")
        return code
    say.write(reads.query(reads.Inbox(paper=paper)))
    return 0


def _sync_command(commands: Any) -> None:
    """`sync`: the author's round, the machine's part of it."""
    sync = commands.add_parser(
        "sync",
        help="after an edit, bring the graph and its review up to date on one "
        "budget, then show your inbox (docs/guide.md § 7)",
    )
    paper_arguments(sync)
    sync.add_argument(
        "--store",
        type=Path,
        help=STORE_HELP,
    )
    sync.add_argument(
        "--profile",
        help=f"the profile both steps ask under (default: {seating.DEFAULT_PROFILE})",
    )
    sync.add_argument("--profiles", type=Path, help="a profiles file of your own")
    sync.add_argument(
        "--session",
        action="append",
        default=[],
        choices=reviewing.SESSION_CHOICES,
        metavar="SESSION",
        help=reviewing.SESSION_HELP,
    )
    sync.add_argument(
        "--budget",
        type=float,
        help="dollars the round may spend across its steps (default: the "
        "budget `axiomation settings` shows)",
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help=f"{DRY_RUN_HELP}; before the update is kept, the review is priced "
        "at the latest kept state",
    )
    prompts_argument(sync)
    updating.no_reread_argument(sync)
    no_search_argument(sync)
    no_circles_argument(sync)


#: `sync`'s arguments.
ARGUMENTS: dict[str, Callable[[Any], None]] = {"sync": _sync_command}


def _under(prompts: Path | None, step: str) -> Path | None:
    """The directory a step's prompts go to: its own under `--prompts`."""
    return None if prompts is None else prompts / step


def _latest(store: RecordStore) -> State:
    # the latest a step keeps on, one waiting for the person's word among
    # them, as `kept_on` reads it
    found = versions.latest(store, waiting=True)
    assert found is not None
    return found
