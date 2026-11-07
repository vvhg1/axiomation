"""`axiomation sync`: the author's round, the machine's part of it.
`update` where the paper's files are ahead of the latest state, then
`review` on what is left of one budget, then the inbox. The
steps are their own commands' runs, tested where they live; here, the
chaining, the budget across it, and where it stops. No model is called.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation import cli
from axiomation.commands import Exit, Ran, Say, asks, reads
from axiomation.commands import review as reviewing
from axiomation.commands import update as update_command
from axiomation.records import RecordStore
from axiomation.runner import LikeRuns
from axiomation.settings import CONFIG_NAME
from test_edits import built_state
from user_files import write_user_file


@pytest.fixture
def kept(tmp_path, capsys) -> tuple[RecordStore, list[str]]:
    store, args = built_state(tmp_path, capsys)
    return store, [args[0], "--store", str(store.directory)]


class Steps:
    """The update, the search and the review, recorded in place of their
    runs."""

    def __init__(self, monkeypatch, update: int = 0, review: int = 0) -> None:
        self.update: update_command.Update | None = None
        self.review: reviewing.Review | None = None
        self.support: asks.Support | None = None

        def searched(asked: asks.Support, say: Say) -> asks.Step:
            self.support = asked
            return asks.Step("state x holds no claim standing unsupported")

        def ran_update(asked: update_command.Update, say: Say) -> Ran:
            self.update = asked
            return Ran(update)

        def ran_review(asked: reviewing.Review, say: Say) -> Ran:
            self.review = asked
            return Ran(review)

        monkeypatch.setattr(update_command, "run", ran_update)
        monkeypatch.setattr(reviewing, "run", ran_review)
        monkeypatch.setattr(asks, "support", searched)


def ahead(monkeypatch) -> None:
    monkeypatch.setattr(reads, "files_version", lambda *given: "an edit")


def test_a_round_asks_a_budget_or_the_paper_s_default(kept, capsys) -> None:
    _, where = kept
    assert cli.main(["sync", *where]) == 1
    err = capsys.readouterr().err
    assert "asking a model needs --budget (--dry-run prices the round first)" in err
    assert "or set run_dollars_max in the user file" in err
    assert (
        f"or [budget].run_dollars_max in the paper's or the workspace's {CONFIG_NAME}"
        in err
    )


def test_files_at_the_latest_state_go_straight_to_the_review(
    kept, capsys, monkeypatch
) -> None:
    _, where = kept
    steps = Steps(monkeypatch)
    assert cli.main(["sync", *where, "--budget", "4"]) == 0
    out = capsys.readouterr().out
    assert steps.update is None
    assert "no update" in out and "sync: review\n" in out
    assert steps.review is not None
    assert not steps.review.all_again and steps.review.session == ()
    assert steps.review.asking.budget == 4.0
    assert steps.review.asking.expect is None
    # the round ends in the inbox
    assert "sync: $0.00 spent of $4" in out
    assert "inbox at state" in out and "1 decision of yours" in out


def test_an_edit_is_updated_first_and_the_review_takes_what_is_left(
    kept, capsys, monkeypatch
) -> None:
    _, where = kept
    ahead(monkeypatch)
    steps = Steps(monkeypatch)
    assert cli.main(["sync", *where, "--budget", "4", "--no-reread"]) == 0
    assert steps.update is not None and steps.review is not None
    assert steps.update.asking.budget == 4.0 and steps.update.no_reread
    assert not steps.update.no_circles and not steps.update.dry_run
    assert steps.review.asking.budget == 4.0
    assert "sync: update, the paper's text ahead of state" in capsys.readouterr().out


def test_an_update_not_whole_stops_the_round(kept, capsys, monkeypatch) -> None:
    _, where = kept
    ahead(monkeypatch)
    steps = Steps(monkeypatch, update=Exit.UNANSWERED)
    assert cli.main(["sync", *where, "--budget", "4"]) == Exit.UNANSWERED
    assert steps.review is None
    assert "stopped after the update, $0.00 spent: the same command resumes" in (
        capsys.readouterr().err
    )


def test_a_review_not_whole_ends_the_round_without_the_inbox(
    kept, capsys, monkeypatch
) -> None:
    _, where = kept
    Steps(monkeypatch, review=Exit.STOPPED)
    assert cli.main(["sync", *where, "--budget", "4"]) == Exit.STOPPED
    captured = capsys.readouterr()
    assert "stopped in the review: the same command resumes" in captured.err
    assert "inbox at state" not in captured.out


def test_the_paper_s_default_is_taken_said_and_handed_on(
    kept, capsys, monkeypatch
) -> None:
    _, where = kept
    (Path(where[0]) / CONFIG_NAME).write_text("[budget]\nrun_dollars_max = 3\n")
    ahead(monkeypatch)
    steps = Steps(monkeypatch)
    assert cli.main(["sync", *where]) == 0
    assert "axiomation sync: budget $3, the paper's [budget].run_dollars_max" in (
        capsys.readouterr().err
    )
    assert steps.update is not None and steps.review is not None
    assert steps.update.asking.budget == 3.0
    assert isinstance(steps.update.asking.expect, LikeRuns)
    assert isinstance(steps.review.asking.expect, LikeRuns)


def test_the_person_s_default_is_taken_where_the_paper_sets_none(
    kept, capsys, monkeypatch, user_file
) -> None:
    _, where = kept
    write_user_file(user_file, run_dollars_max=4)
    ahead(monkeypatch)
    steps = Steps(monkeypatch)
    assert cli.main(["sync", *where]) == 0
    assert "axiomation sync: budget $4, the user file's run_dollars_max" in (
        capsys.readouterr().err
    )
    assert steps.update is not None
    assert steps.update.asking.budget == 4.0


def test_a_dry_run_prices_the_round_and_asks_nothing(
    kept, capsys, tmp_path, monkeypatch
) -> None:
    _, where = kept
    Steps(monkeypatch)
    assert (
        cli.main(["sync", *where, "--dry-run", "--prompts", str(tmp_path / "dry")]) == 0
    )
    out = capsys.readouterr().out
    assert "no update" in out and "sync: review\n" in out
    assert "sync: the round, expected $0.00 (" in out and "partial" not in out


def test_a_dry_run_takes_no_directory_and_prompts_go_to_each_step(
    kept, capsys, tmp_path, monkeypatch
) -> None:
    """`--dry-run` alone prices the round and hands no step a directory;
    `--prompts DIR` gives each ask its own under DIR, the update
    `DIR/update`, and the review DIR itself, a session's under it."""
    _, where = kept
    steps = Steps(monkeypatch)
    assert cli.main(["sync", *where, "--dry-run"]) == 0
    assert "sync: the round, expected $0.00 (" in capsys.readouterr().out
    assert steps.support is not None and steps.review is not None
    assert steps.support.dry_run and steps.support.prompts is None
    assert steps.review.dry_run and steps.review.prompts is None

    prompts = tmp_path / "prompts"
    assert cli.main(["sync", *where, "--dry-run", "--prompts", str(prompts)]) == 0
    assert steps.support.prompts == prompts / "support"
    assert steps.review.prompts == prompts

    ahead(monkeypatch)
    assert cli.main(["sync", *where, "--dry-run", "--prompts", str(prompts)]) == 0
    assert steps.update is not None
    assert steps.update.dry_run and steps.update.prompts == prompts / "update"


def test_a_dry_run_says_the_round_is_partial_where_the_search_reads(
    kept, capsys, tmp_path, monkeypatch
) -> None:
    """The review is priced before the search files what it reads
    unargued, so a round whose search reads claims says it leaves them
    out, and how many."""
    _, where = kept
    Steps(monkeypatch)
    monkeypatch.setattr(
        asks, "support", lambda asked, say: asks.Step("priced", adds_subjects=12)
    )
    assert (
        cli.main(["sync", *where, "--dry-run", "--prompts", str(tmp_path / "dry")]) == 0
    )
    out = capsys.readouterr().out
    assert "; partial: the search reads 12 claims" in out


def test_a_reread_the_update_left_unanswered_is_asked_again_and_stops_the_round(
    kept, capsys, monkeypatch
) -> None:
    """The files at the latest state, so no update: the reread step still
    runs, and where its session is not answered the round stops there,
    the review not asked; the same command asks it again."""
    _, where = kept
    steps = Steps(monkeypatch)
    asked: list[str] = []

    def reread(request, store, state, say):
        asked.append(state.id)
        return asks.Step("unanswered: no reread filed", whole=False)

    monkeypatch.setattr(asks, "reread_asked", reread)
    assert cli.main(["sync", *where, "--budget", "4"]) == Exit.UNANSWERED
    captured = capsys.readouterr()
    assert "sync: reread --ask" in captured.out
    assert "stopped at reread --ask, $0.00 spent: the same command resumes" in (
        captured.err
    )
    assert asked and steps.review is None
    assert cli.main(["sync", *where, "--budget", "4", "--no-reread"]) == 0
    assert steps.review is not None and len(asked) == 1


def test_the_circles_step_runs_on_what_is_left(kept, capsys, monkeypatch) -> None:
    _, where = kept
    Steps(monkeypatch)
    given: list[float] = []

    def circled(asked, say):
        given.append(asked.asking.budget)
        return asks.Step("state x holds no circle to ask")

    monkeypatch.setattr(asks, "circles_of", circled)
    assert cli.main(["sync", *where, "--budget", "4"]) == 0
    assert given == [4.0]
    assert "sync: circles --ask" in capsys.readouterr().out


def test_a_dry_run_with_an_edit_prices_the_end_steps_in_the_update(
    kept, capsys, monkeypatch, tmp_path
) -> None:
    """An update that keeps the new files alone sets no price: the round
    is still priced, and the reread and circles are the update's."""
    _, where = kept
    ahead(monkeypatch)
    steps = Steps(monkeypatch)
    assert (
        cli.main(["sync", *where, "--dry-run", "--prompts", str(tmp_path / "dry")]) == 0
    )
    out = capsys.readouterr().out
    assert steps.update is not None and steps.update.dry_run
    assert "sync: reread --ask: priced in the update's dry run, if it fires" in out
    assert "estimate $0.00; partial: the review is priced at state" in out


def test_the_search_runs_between_the_reread_and_the_circles(
    kept, capsys, monkeypatch
) -> None:
    """At the latest state, `support --ask` runs on what is left of the
    budget after the reread and before the circles; `--no-search` skips it
    and hands it on to the update."""
    _, where = kept
    steps = Steps(monkeypatch)
    assert cli.main(["sync", *where, "--budget", "4"]) == 0
    out = capsys.readouterr().out
    assert steps.support is not None and steps.support.asking.budget == 4.0
    assert (
        out.index("sync: reread --ask")
        < out.index("sync: support --ask")
        < out.index("sync: circles --ask")
    )
    steps = Steps(monkeypatch)
    ahead(monkeypatch)
    assert cli.main(["sync", *where, "--budget", "4", "--no-search"]) == 0
    assert steps.support is None
    assert steps.update is not None and steps.update.no_search


def test_a_round_asks_the_sessions_it_names(kept, capsys, monkeypatch) -> None:
    """`--session` names the review sessions the round asks, a group among
    them, as `review --session` does; the earlier name of one is no
    choice of the command line."""
    _, where = kept
    steps = Steps(monkeypatch)
    named = ["--session", "argument", "--session", "field"]
    assert cli.main(["sync", *where, "--budget", "4", *named]) == 0
    assert steps.review is not None
    assert steps.review.session == ("argument", "field")
    with pytest.raises(SystemExit):
        cli.main(["sync", *where, "--budget", "4", "--session", "citation"])


def test_a_session_the_profile_seats_not_is_refused_before_any_step(
    kept, capsys, monkeypatch
) -> None:
    """A session named that the profile seats no reviewer for, or a group
    with no seated member, is refused before the update or any ask pays,
    not by the review after them."""
    _, where = kept
    ahead(monkeypatch)
    steps = Steps(monkeypatch)
    asked = ["--budget", "4", "--profile", "cheap", "--session", "argument"]
    assert cli.main(["sync", *where, *asked]) == 1
    assert steps.update is None and steps.review is None
    assert "seats no reviewer for argument" in capsys.readouterr().err
    asked[-1] = "field"
    assert cli.main(["sync", *where, *asked]) == 1
    assert steps.update is None and steps.review is None
    err = capsys.readouterr().err
    assert "seats no reviewer for contribution, fieldresults, relatedwork" in err
