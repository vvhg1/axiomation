"""The cut review's driver, on the synthetic paper with a fake
host. No model is called."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

import record_file
from axiomation import run_state
from axiomation.assembler import corrected_cut
from axiomation.commands.build import _decide_counts, _round_counts
from axiomation.cutter.model import GroupKind, Role, Segment
from axiomation.driver import (
    DROPPED,
    DROPPED_TWICE,
    EDGES_UNANSWERED,
    NO_ROUND,
    ROLES_UNANSWERED,
    STEP_ROLES_UNANSWERED,
    ReviewSettings,
    near_group,
    review_cut,
    review_payload,
    weigh,
)
from axiomation.exchange import dumps
from axiomation.questions import Stage, cut_file
from axiomation.questions.cut_file import DECLINED, PROPOSED, SPLIT, settled
from axiomation.questions.edges_file import EDGES_FILE, LINES_FILE, REASK_KICKOFF
from axiomation.questions.front_lines import shown_file
from axiomation.questions.groups_file import GROUPS_FILE
from axiomation.reader import document_hash
from axiomation.records import CorrectionClass, CutCorrection, CutOp, RecordStore
from axiomation.resolver import Resolver
from axiomation.run_state import RunFile
from axiomation.runner import Runner, Seat, Stop
from axiomation.runner.hosts import Rates
from questions.review_paper import make_paper
from questions.test_edges_file import done, written
from runner.fakes import FakeHost, fenced, shown, stage_file

FLAT = Rates(input=0.0, output=1.0)  # $0.006 an attempt
EMPTY = fenced({"corrections": []})
#: One block per chunk, and no context: ten author sessions, ten judge
#: ones (one per author chunk), and the groups session.
BOTH = (Stage.BOUNDARIES, Stage.GROUPS)
SETTINGS = ReviewSettings(chunk=0, overlap=0, judge=True, stages=BOTH)

SPLIT_AT = "Hence the output is bounded."
SECOND_AT = "A second sentence of the setting."
LEDGER = f"⟦{DECLINED} {SPLIT}⟧"


def regroup(text: str) -> str:
    """The groups session's edit: the proof made a derivation, which names
    nothing (what a group justifies is an edge's)."""
    return text.replace("⟦group proof ", "⟦group derivation ")


def groups_files(fake: FakeHost) -> list[str]:
    """The `groups.md` files the host was given, in order."""
    return [shown(w) for w in fake.workspaces if stage_file(w) == GROUPS_FILE]


def edit(text: str) -> str:
    """The author splits the proof's first paragraph before its
    conclusion; the judge keeps every edit (its file flags them)."""
    if PROPOSED in text:
        return text
    if "By Definition~" in text:
        return text.replace(SPLIT_AT, f"⟦new⟧\n{SPLIT_AT}")
    return text


def declining(text: str) -> str:
    """The author splits; the judge declines the split. The next author
    respects the ledger it leaves and proposes nothing."""
    if PROPOSED in text:
        return text.replace(PROPOSED, f"{DECLINED} — short verification")
    if LEDGER in text:
        return text
    if "By Definition~" in text:
        return text.replace(SPLIT_AT, f"⟦new⟧\n{SPLIT_AT}")
    return text


def answer(prompt: str) -> str:
    return EMPTY


def host(**options) -> FakeHost:
    """Cuts as `edit`; each groups session as `regroup`."""
    return FakeHost(
        answer, edit=options.pop("edit", edit), edits={GROUPS_FILE: regroup}, **options
    )


def is_proof(question) -> bool:
    return "By Definition~" in shown_file(question)


@pytest.fixture
def paper(tmp_path: Path):
    return make_paper(tmp_path / "paper")


def review(paper, directory: Path, host=None, budget=10.0, jobs=1, settings=SETTINGS):
    store = RecordStore(directory)
    # the run holds the texts of the answers it asks, as the command's does
    run = RunFile(directory, "build", document_hash(paper.document), "")
    runner = None
    if host is not None:
        runner = Runner(host, Resolver(store, run=run), budget, FLAT, jobs=jobs)
    found = review_cut(paper.document, paper.cut, store, settings, runner, run)
    return found, dumps(review_payload(paper.document, found, settings))


def test_both_stages_are_asked_and_applied_in_order(paper, tmp_path) -> None:
    fake = host()

    found, _ = review(paper, tmp_path / "store", fake)

    assert found.complete and found.failed == () and found.stopped is None
    assert len(found.questions[Stage.BOUNDARIES]) == 10
    assert len(found.questions[Stage.JUDGE]) == 10  # the author's chunks
    # A groups session per chunk of the corrected cut, one block each: no
    # chunk holds the whole proof, so its group stays as the cutter proposed.
    assert len(found.questions[Stage.GROUPS]) == 11
    assert fake.calls == 10 + 10 + 11
    cut = found.corrected.cut
    split_at = paper.id_at("Hence the output is bounded.")
    assert split_at in [b.id for b in cut.blocks]
    (proof,) = cut.groups
    assert proof.kind is GroupKind.PROOF
    assert proof.blocks[-1] == paper.id_at("Next, the bound is sharp.")
    assert found.corrected.problems == ()
    assert len(found.corrected.applied) == 1


def test_a_chunk_that_holds_a_group_corrects_it(paper, tmp_path) -> None:
    """The whole paper in one chunk: the groups session sees the proof and
    makes it a derivation, which names nothing."""
    settings = ReviewSettings(chunk=None, overlap=0, stages=BOTH)

    found, _ = review(paper, tmp_path / "store", host(), settings=settings)

    (group,) = found.corrected.cut.groups
    assert group.kind is GroupKind.DERIVATION and group.names is None
    assert group.blocks[-1] == paper.id_at("Next, the bound is sharp.")


def test_the_judge_s_answer_is_recorded_as_the_judge_s(paper, tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    runner = Runner(host(), Resolver(store), 10.0, FLAT, jobs=1)

    review_cut(paper.document, paper.cut, store, SETTINGS, runner)

    judge = [a for a in store.answers() if a.stage == "judge"]
    assert len(judge) == 10
    assert all(a.judges for a in judge)
    author = [a for a in store.answers() if a.stage == "boundaries"]
    assert all(a.judges is None for a in author)


def test_a_judge_on_a_host_of_its_own_answers_the_judge_s_questions(
    paper, tmp_path
) -> None:
    """The mixed route: the judge's questions go to its host,
    every other to the author's, and each answer records the host, model
    and effort that gave it. A replay is the run, byte for byte."""
    author = host(model="opencode/deepseek-v4.1-flash")
    judge = host(model="haiku")
    judge.name, judge.effort = "claude-code 2.1.285", "low"
    store = RecordStore(tmp_path / "store")
    runner = Runner(
        author,
        Resolver(store),
        10.0,
        FLAT,
        jobs=1,
        seats={Stage.JUDGE: Seat(judge, FLAT)},
    )

    found = review_cut(paper.document, paper.cut, store, SETTINGS, runner)

    assert found.complete
    assert {w.edit for w in judge.workspaces} == {"cut.md"}
    answered = {}
    for answer in store.answers():
        answered.setdefault(answer.stage, set()).add(
            (answer.host, answer.model, answer.effort)
        )
    assert answered == {
        "boundaries": {("fake", "opencode/deepseek-v4.1-flash", None)},
        "judge": {("claude-code 2.1.285", "haiku", "low")},
        "groups": {("fake", "opencode/deepseek-v4.1-flash", None)},
    }
    replayed = review_cut(paper.document, paper.cut, store, SETTINGS)
    assert dumps(review_payload(paper.document, replayed, SETTINGS)) == dumps(
        review_payload(paper.document, found, SETTINGS)
    )


def test_the_groups_session_is_asked_on_the_corrected_cut(paper, tmp_path) -> None:
    fake = host()

    review(paper, tmp_path / "store", fake)

    files = groups_files(fake)
    assert len(files) == 11  # ten blocks, and the split's second piece
    assert any(t.split("\n", 3)[2:3] == ["⟦b1 none⟧"] for t in files)
    assert any("⟦b1 none⟧\nHence the output is bounded." in t for t in files)


def test_the_groups_session_waits_while_a_chunk_is_unsettled(paper, tmp_path) -> None:
    """The run stops at its budget inside the author's pass: the judge's
    questions and the groups session are not asked, and stay unanswered."""
    fake = host(cost=0.006)

    found, _ = review(paper, tmp_path / "store", fake, budget=0.03)

    assert found.stopped is not None and found.stopped.stopped is Stop.BUDGET
    assert fake.calls == 5
    assert not groups_files(fake)
    assert found.questions[Stage.GROUPS][0].key in found.unanswered
    assert not found.complete


def test_a_failed_chunk_is_left_as_proposed(paper, tmp_path) -> None:
    """The proof's chunk fails its three attempts; the groups sessions
    are asked all the same (the boundary stage is settled), on a cut where
    that block is as the cutter proposed it."""

    def failing(text: str) -> str:
        return "I would rather not." if "By Definition~" in text else text

    found, _ = review(paper, tmp_path / "store", host(edit=failing))

    proof_window = next(q for q in found.questions[Stage.BOUNDARIES] if is_proof(q))
    assert found.failed == (proof_window.key,)
    assert found.unanswered == (proof_window.key,)
    assert [b.id for b in found.corrected.cut.blocks] == [
        b.id for b in paper.cut.blocks
    ]
    # The groups chunk as the boundaries do.
    assert len(found.questions[Stage.GROUPS]) == len(found.questions[Stage.BOUNDARIES])


def test_a_replay_from_the_store_is_byte_identical(paper, tmp_path) -> None:
    _, run = review(paper, tmp_path / "store", host())

    replay, again = review(paper, tmp_path / "store")

    assert again == run
    assert replay.reports == () and replay.complete


def test_a_stopped_and_resumed_run_gives_the_same_export(paper, tmp_path) -> None:
    """Stopped at its budget, then resumed: no key asked twice, and the
    export equals an uninterrupted run's and a four-job run's."""
    whole_host = host()
    _, whole = review(paper, tmp_path / "whole", whole_host)
    _, four = review(paper, tmp_path / "four", host(), jobs=4)

    first = host(cost=0.006)
    stopped, _ = review(paper, tmp_path / "resumed", first, budget=0.03)
    second = host(cost=0.006)
    resumed, export = review(paper, tmp_path / "resumed", second)

    assert stopped.stopped is not None
    assert first.calls + second.calls == whole_host.calls
    answers = RecordStore(tmp_path / "resumed").answers()
    assert len(answers) == len({a.key for a in answers})
    assert resumed.complete
    assert export == whole == four


def test_a_different_chunk_setting_is_a_different_review(paper, tmp_path) -> None:
    """Keys hold their chunks: a store answered at one chunk size gives
    nothing to another."""
    review(paper, tmp_path / "store", host())

    other, _ = review(
        paper, tmp_path / "store", settings=ReviewSettings(chunk=300, stages=BOTH)
    )

    assert len(other.unanswered) == len(other.questions[Stage.BOUNDARIES]) + len(
        other.questions[Stage.GROUPS]
    )


def test_the_export_s_review_names_what_a_replay_can_know(paper, tmp_path) -> None:
    found, text = review(paper, tmp_path / "store", host())

    payload = json.loads(text)
    assert payload["producer"] == "axiomation review-cut"
    assert payload["review"]["complete"] is True
    assert (payload["review"]["chunk"], payload["review"]["overlap"]) == (0, 0)
    assert payload["review"]["rounds"] == 1
    assert payload["review"]["questions"] == {
        "boundaries": 10,
        "judge": 10,
        "groups": 11,
    }
    assert payload["review"]["unanswered"] == []
    assert set(payload["review"]["templates"]) == {
        "cut-review-file",
        "cut-review-judge",
        "groups-lines",
    }
    assert payload["review"]["applied"] == list(found.corrected.applied)


def test_the_budget_covers_both_stages(paper, tmp_path) -> None:
    """Ten author attempts at $0.006 leave less than one judge attempt of
    a $0.065 budget: the judge is asked nothing, and the groups session
    waits."""
    fake = host(cost=0.006)

    found, _ = review(paper, tmp_path / "store", fake, budget=0.065)

    assert fake.calls == 10
    assert found.stopped is not None and found.stopped.stopped is Stop.BUDGET
    assert sum(r.spent for r in found.reports) == pytest.approx(0.06)
    assert found.questions[Stage.GROUPS][0].key in found.unanswered


def test_the_groups_session_is_the_same_whichever_stages_are_asked(paper, tmp_path):
    review(paper, tmp_path / "store", host())

    both, _ = review(paper, tmp_path / "store")
    alone, _ = review(
        paper,
        tmp_path / "store",
        settings=ReviewSettings(0, 0, stages=(Stage.GROUPS,)),
    )

    assert alone.questions[Stage.GROUPS][0].key == both.questions[Stage.GROUPS][0].key
    assert alone.complete


def test_a_second_round_is_asked_on_the_first_s_cut(paper, tmp_path) -> None:
    """Round 1 splits the proof's paragraph and the judge keeps it; round 2
    asks every chunk of the corrected cut again, and corrects nothing (its
    split is already there), so round 3 is never built."""
    fake = host()
    settings = ReviewSettings(chunk=0, overlap=0, rounds=3, judge=True, stages=BOTH)

    found, _ = review(paper, tmp_path / "store", fake, settings=settings)

    assert len(found.questions[Stage.BOUNDARIES]) == 10 + 11  # round 2 on the split cut
    assert len(found.questions[Stage.JUDGE]) == 10
    assert fake.calls == 10 + 10 + 11 + 11
    assert found.complete and len(found.corrected.applied) == 1
    replay, _ = review(paper, tmp_path / "store", settings=settings)
    assert replay.corrected.cut == found.corrected.cut


def test_a_round_that_is_not_answered_whole_ends_the_rounds(paper, tmp_path) -> None:
    fake = host(cost=0.006)
    settings = ReviewSettings(chunk=0, overlap=0, rounds=3, stages=BOTH)

    found, _ = review(paper, tmp_path / "store", fake, budget=0.03, settings=settings)

    assert len(found.questions[Stage.BOUNDARIES]) == 10
    assert fake.calls == 5


def test_a_declined_edit_is_reverted_and_its_reason_reaches_the_next_author(
    paper, tmp_path
) -> None:
    """The judge declines the author's split: the cut is back where the
    round started, the round goes on (the ledger is new), and the next
    author's file shows the rejection where it applies."""
    settings = ReviewSettings(chunk=0, overlap=0, rounds=2, judge=True, stages=BOTH)

    found, _ = review(
        paper, tmp_path / "store", host(edit=declining), settings=settings
    )

    assert [b.id for b in found.corrected.cut.blocks] == [
        b.id for b in paper.cut.blocks
    ]
    ledgers = [q for q in found.questions[Stage.BOUNDARIES] if LEDGER in shown_file(q)]
    assert len(ledgers) == 1
    assert settled(SPLIT, "short verification") in shown_file(ledgers[0])
    replay, _ = review(paper, tmp_path / "store", settings=settings)
    assert replay.corrected.cut == found.corrected.cut


def test_the_ledger_is_made_from_the_declines_filed_not_from_the_text(
    paper, tmp_path, monkeypatch
) -> None:
    """A judge's declines are filed with its answer as its check settled
    them; the run read again from the store builds the same next author's
    file from them, never reading the judge's text."""
    settings = ReviewSettings(chunk=0, overlap=0, rounds=2, judge=True, stages=BOTH)
    found, _ = review(
        paper, tmp_path / "store", host(edit=declining), settings=settings
    )
    store = RecordStore(tmp_path / "store")
    filed = [d for q in found.questions[Stage.JUDGE] for d in store.declines_of(q.key)]
    assert [d.line for d in filed] == [settled(SPLIT, "short verification")]

    def unread(*args):
        raise AssertionError("a judge's text read again")

    monkeypatch.setattr(cut_file, "declines", unread)
    replay, _ = review(paper, tmp_path / "store", settings=settings)
    assert [shown_file(q) for q in replay.questions[Stage.BOUNDARIES]] == [
        shown_file(q) for q in found.questions[Stage.BOUNDARIES]
    ]


def declining_twice(text: str) -> str:
    """One session holds the paper. The judge declines every edit; the
    author splits at a first place, then, once that is declined, at a
    second, and then proposes nothing."""
    if PROPOSED in text:
        return text.replace(PROPOSED, f"{DECLINED} — no")
    seen = text.count(LEDGER)
    if seen == 0:
        return text.replace(SPLIT_AT, f"⟦new⟧\n{SPLIT_AT}")
    if seen == 1:
        return text.replace(SECOND_AT, f"⟦new⟧\n{SECOND_AT}")
    return text


def test_the_ledger_keeps_every_round_s_declines(paper, tmp_path) -> None:
    """Declines are settled: the third author sees both earlier rounds'
    declines, not only the last one's, and proposes nothing."""
    settings = ReviewSettings(chunk=None, overlap=0, rounds=4, judge=True, stages=BOTH)

    found, _ = review(
        paper, tmp_path / "store", host(edit=declining_twice), settings=settings
    )

    authors = found.questions[Stage.BOUNDARIES]
    assert [shown_file(q).count(LEDGER) for q in authors] == [0, 1, 2]
    assert [b.id for b in found.corrected.cut.blocks] == [
        b.id for b in paper.cut.blocks
    ]


def test_the_cuts_alone_are_asked_by_default(paper, tmp_path) -> None:
    fake = host()

    found, _ = review(
        paper, tmp_path / "store", fake, settings=ReviewSettings(chunk=0, overlap=0)
    )

    assert list(found.questions) == [Stage.BOUNDARIES]
    assert fake.calls == 10 and fake.workspaces
    assert found.complete


def test_without_the_judge_a_second_round_is_a_second_author_pass(
    paper, tmp_path
) -> None:
    """The judge is off by default. Round 2 asks every chunk of
    round 1's cut again and corrects nothing, so round 3 is never built."""
    fake = host()
    settings = ReviewSettings(chunk=0, overlap=0, rounds=3)

    found, _ = review(paper, tmp_path / "store", fake, settings=settings)

    assert list(found.questions) == [Stage.BOUNDARIES]
    assert len(found.questions[Stage.BOUNDARIES]) == 10 + 11
    assert fake.calls == 10 + 11
    assert paper.id_at(SPLIT_AT) in [b.id for b in found.corrected.cut.blocks]
    replay, _ = review(paper, tmp_path / "store", settings=settings)
    assert replay.corrected.cut == found.corrected.cut


# --- the roles session -------------------------------------------------

SESSION = ReviewSettings(chunk=None, overlap=0, stages=(Stage.ROLES,), given_cut=True)


def roles_edit(text: str) -> str:
    """Every block a claim, and the proof's conclusion a span of its own
    after a justification."""
    if "⟦b1⟧" not in text:
        return edit(text)
    text = re.sub(r"^⟦(b\d+)⟧$", r"⟦\1 claim⟧", text, flags=re.MULTILINE)
    text = text.replace(
        "Hence the output is bounded.",
        "⟦+ justification⟧\nHence the output is bounded.",
    )
    return text


def test_the_roles_session_gives_every_block_its_roles(paper, tmp_path) -> None:
    fake = FakeHost(answer, edit=roles_edit)

    found, _ = review(paper, tmp_path / "store", fake, settings=SESSION)

    (question,) = found.questions[Stage.ROLES]
    assert question.workspace is not None and fake.calls == 1
    assert found.complete and found.corrected.problems == ()
    cut = found.corrected.cut
    assert all(b.roles for b in cut.blocks)
    proof = cut.block(paper.id_at(r"\begin{proof}"))
    assert proof.roles == (Role.CLAIM, Role.JUSTIFICATION)
    assert proof.segments == (
        Segment("", Role.CLAIM),
        Segment("Hence the output", Role.JUSTIFICATION),
    )
    assert [b.id for b in cut.blocks] == [b.id for b in paper.cut.blocks]


def test_the_roles_session_replays_byte_for_byte(paper, tmp_path) -> None:
    _, run = review(
        paper, tmp_path / "store", FakeHost(answer, edit=roles_edit), settings=SESSION
    )

    replay, again = review(paper, tmp_path / "store", settings=SESSION)

    assert again == run and replay.complete and replay.reports == ()
    assert json.loads(run)["review"]["role_passes"] == 1


@pytest.mark.usefixtures("develop_mode")
def test_sessions_that_write_lines_are_laid_and_applied(paper, tmp_path) -> None:
    """The sessions as a model answers them: a cut with nothing to correct
    and a paper read as holding no group each write the one line `none`;
    the cut stays, the proposed group goes, and the store keeps each stage's
    file, the lines laid into it."""
    settings = ReviewSettings(chunk=None, overlap=0, stages=BOTH)
    fake = FakeHost(
        answer, edits={"cut.md": lambda t: "none\n", GROUPS_FILE: lambda t: "none\n"}
    )

    found, _ = review(paper, tmp_path / "store", fake, settings=settings)

    assert found.complete, found.unanswered
    assert [b.id for b in found.corrected.cut.blocks] == [
        b.id for b in paper.cut.blocks
    ]
    assert found.corrected.cut.groups == ()
    answers = RecordStore(tmp_path / "store").answers()
    assert [a.valid for a in answers] == [True, True]
    # the files, laid
    texts = [record_file.text(tmp_path / "store", a) for a in answers]
    assert all(t.startswith("##### ") for t in texts)


@pytest.mark.usefixtures("develop_mode")
def test_a_session_that_leaves_a_role_unset_is_asked_again(paper, tmp_path) -> None:
    """The first attempt leaves `?`; the re-ask fills it."""
    calls = []

    def partly(text: str) -> str:
        calls.append(text)
        if len(calls) == 1:
            return roles_edit(text).replace("⟦b5 claim⟧", "⟦b5⟧")
        return text.replace("⟦b5⟧", "⟦b5 claim⟧")  # it fixes what was named

    found, _ = review(
        paper, tmp_path / "store", FakeHost(answer, edit=partly), settings=SESSION
    )

    assert len(calls) == 2 and found.complete
    answers = RecordStore(tmp_path / "store").answers()
    assert [a.valid for a in answers] == [False, True]
    assert any("b5 has no role yet" in p for p in answers[0].problems)
    # The re-ask starts from the refused edit, which it fixes.
    assert calls[1] == record_file.text(tmp_path / "store", answers[0])
    assert "⟦b1 claim⟧" in calls[1] and "⟦b5⟧" in calls[1]


def test_a_boundary_session_is_reasked_from_its_refused_answer(paper, tmp_path) -> None:
    """An author pass answers as lines, so its re-ask starts from what it
    wrote, the problems named, as every session that answers so."""
    seen = []

    def broken(text: str) -> str:
        return text.replace("⟦b1 ", "⟦x ", 1).replace("#####", "####", 1)

    def deleting_the_first(text: str) -> str:
        seen.append(text)
        return broken(text) if len(seen) == 1 else text

    settings = ReviewSettings(chunk=None, overlap=0)
    review(paper, tmp_path / "store", host(edit=deleting_the_first), settings=settings)

    assert seen[1] == broken(seen[0])


def test_a_second_roles_pass_reviews_the_first(paper, tmp_path) -> None:
    """The review makes the theorem a definition; the first pass's answers
    stay in the store, its key unchanged, and the replay is the same."""
    store = tmp_path / "store"
    _, first = review(paper, store, FakeHost(answer, edit=roles_edit), settings=SESSION)
    two = replace(SESSION, role_passes=2)

    def reviewing(text: str) -> str:
        return text.replace("⟦b5 claim⟧", "⟦b5 definition⟧")

    found, run = review(paper, store, FakeHost(answer, edit=reviewing), settings=two)

    first_q, second_q = found.questions[Stage.ROLES]
    assert first_q.workspace.instructions != second_q.workspace.instructions
    answers = RecordStore(store).answers()
    assert len(answers) == 2  # the first pass replayed, the second asked
    theorem = found.corrected.cut.block(paper.id_at(r"\begin{theorem}"))
    assert theorem.roles == (Role.DEFINITION,)
    first_cut = json.loads(first)
    assert {b["id"]: b["roles"] for b in first_cut["blocks"]}[
        paper.id_at(r"\begin{theorem}")
    ] == ["claim"]
    _, again = review(paper, store, settings=two)
    assert again == run


def claims_edit(text: str) -> str:
    """Every block a claim, nothing more: the edit for a cut a boundary pass
    has split, where a part line may not stand at a block's start."""
    if "⟦b1⟧" not in text:
        return edit(text)
    return re.sub(r"^⟦(b\d+)⟧$", r"⟦\1 claim⟧", text, flags=re.MULTILINE)


def roles_host(**options) -> FakeHost:
    """Cuts as `host` does; roles as `options["roles"]`, `roles_edit` by
    default."""
    return FakeHost(
        answer,
        edit=options.pop("roles", roles_edit),
        edits={GROUPS_FILE: regroup},
        **options,
    )


ON_THE_CUTTER = ReviewSettings(chunk=None, overlap=0, stages=(Stage.ROLES,))
CUTS = ReviewSettings(chunk=None, overlap=0)


def test_on_the_cutter_s_cut_the_roles_wait_for_the_boundary_answers(
    paper, tmp_path
) -> None:
    """As the groups do: the roles alone ask nothing until the store holds
    the boundary stage's answers, then ask on the cut they leave."""
    fake = roles_host(roles=claims_edit)

    waiting, _ = review(paper, tmp_path / "store", fake, settings=ON_THE_CUTTER)
    assert fake.calls == 0 and not waiting.complete

    review(paper, tmp_path / "store", host(), settings=CUTS)
    found, _ = review(paper, tmp_path / "store", fake, settings=ON_THE_CUTTER)

    (question,) = found.questions[Stage.ROLES]
    assert paper.id_at("Hence the output is bounded.") in question.handles.values()
    assert fake.calls == 1 and found.complete


def test_the_roles_are_asked_on_the_cut_the_boundaries_left(paper, tmp_path):
    settings = replace(CUTS, stages=(Stage.BOUNDARIES, Stage.ROLES))

    found, _ = review(
        paper, tmp_path / "store", roles_host(roles=claims_edit), settings=settings
    )

    (question,) = found.questions[Stage.ROLES]
    blocks = [h for h in question.handles if h.startswith("b")]
    assert len(blocks) == len(paper.cut.blocks) + 1  # the author's split
    assert found.complete


def test_the_roles_wait_while_a_chunk_is_unsettled(paper, tmp_path) -> None:
    settings = ReviewSettings(
        chunk=0, overlap=0, stages=(Stage.BOUNDARIES, Stage.ROLES)
    )
    fake = roles_host(cost=0.006)

    found, _ = review(paper, tmp_path / "store", fake, budget=0.03, settings=settings)

    assert found.stopped is not None
    assert not any("roles.md" in w.files for w in fake.workspaces)


def test_the_groups_session_waits_for_the_roles_and_sees_them(paper, tmp_path):
    """Groups after roles: asked last, on the cut the session left, so its
    key differs from a run without roles where a role changed."""
    settings = replace(SESSION, stages=(Stage.ROLES, Stage.GROUPS))
    fake = roles_host()

    found, _ = review(paper, tmp_path / "store", fake, settings=settings)
    alone, _ = review(
        paper,
        tmp_path / "alone",
        host(),
        settings=replace(SESSION, stages=(Stage.GROUPS,)),
    )

    (text,) = groups_files(fake)
    assert GROUPS_FILE in fake.workspaces[-1].files  # asked last
    assert "⟦b6 claim⟧" in text and "⟦+ justification⟧" in text
    assert found.questions[Stage.GROUPS][0].key != alone.questions[Stage.GROUPS][0].key


def test_a_given_cut_stands_in_place_of_the_boundary_stage(paper, tmp_path) -> None:
    """On a given cut no boundary session is built, and the store's
    boundary answers are not applied."""
    store = tmp_path / "store"
    review(paper, store, host(), settings=ReviewSettings(chunk=0, overlap=0))
    fake = roles_host()

    found, payload = review(paper, store, fake, settings=SESSION)

    assert list(found.questions) == [Stage.ROLES]
    assert all("roles.md" in w.files for w in fake.workspaces)
    assert [b.id for b in found.corrected.cut.blocks] == [
        b.id for b in paper.cut.blocks
    ]  # the author's split, in the store, is not applied
    assert json.loads(payload)["review"]["cut"] == "given"


# --- the groups' passes ---------------------------------------------------


def test_a_later_groups_pass_corrects_the_last_and_one_unchanged_ends_them(
    paper, tmp_path
) -> None:
    """Three passes allowed: the first makes the proof a derivation, the
    second sees the derivation and changes nothing, so no third is built."""
    settings = ReviewSettings(chunk=None, overlap=0, stages=BOTH, group_passes=3)
    fake = host()

    found, run = review(paper, tmp_path / "store", fake, settings=settings)

    first, second = found.questions[Stage.GROUPS]
    assert "⟦group proof " in shown_file(first)
    assert "⟦group derivation " in shown_file(second)
    assert len(groups_files(fake)) == 2 and found.complete
    (group,) = found.corrected.cut.groups
    assert group.kind is GroupKind.DERIVATION
    replay, again = review(paper, tmp_path / "store", settings=settings)
    assert again == run and replay.reports == ()


def test_one_groups_pass_is_the_default(paper, tmp_path) -> None:
    fake = host()
    settings = ReviewSettings(chunk=None, overlap=0, stages=BOTH)

    found, text = review(paper, tmp_path / "store", fake, settings=settings)

    assert len(found.questions[Stage.GROUPS]) == 1 and len(groups_files(fake)) == 1
    assert json.loads(text)["review"]["group_passes"] == 1


# --- the edges ------------------------------------------------------------

EDGES_STAGES = (Stage.GROUPS, Stage.EDGES)
#: On a given cut, as `review-cut --cut … --stage edges` asks them.
EDGES_SETTINGS = ReviewSettings(
    chunk=None,
    overlap=0,
    stages=EDGES_STAGES,
    files=("extra.tex", "main.tex"),
    given_cut=True,
)
PROOF = "group:by-definition-def:gain-the"
WRITER = {
    "by-definition-def:gain-the": [
        '→ uses def:gain ref · "By Definition~\\ref{def:gain}"'
    ],
    PROOF: ['→ justifies thm:bound · "Hence the output is bounded."'],
    "thm:bound": ['⊢ justified · "The output is bounded."'],
}


def write_edges(text: str) -> str:
    return written(text, WRITER, ["Pervasive: setting"])


def edges_host(**options) -> FakeHost:
    """Groups as `regroup`; the edges as `write_edges`."""
    return FakeHost(
        answer, edits={GROUPS_FILE: regroup, EDGES_FILE: write_edges}, **options
    )


def test_the_edges_session_is_asked_last_on_the_cut_the_groups_left(
    paper, tmp_path
) -> None:
    fake = edges_host()

    found, payload = review(paper, tmp_path / "store", fake, settings=EDGES_SETTINGS)

    assert found.complete and found.stopped is None
    (question,) = found.questions[Stage.EDGES]
    sheet = fake.workspaces[-1].files[EDGES_FILE]
    assert EDGES_FILE in fake.workspaces[-1].files and fake.calls == 2
    assert f"⟦group {PROOF} derivation⟧" in sheet  # the groups' correction
    assert "Files: extra.tex, main.tex" in sheet
    assert [e.kind.value for e in found.edges.edges] == ["uses", "justifies"]
    assert [s.status.value for s in found.edges.standings] == ["justified"]
    assert [f.block for f in found.edges.frame] == [paper.id_at("\\section{Setting}")]
    assert found.edges_answer is not None and found.edges_answer.key == question.key
    data = json.loads(payload)
    assert list(data)[-9:] == [
        "candidates",
        "entries",
        "edges",
        "self",
        "standings",
        "ambient",
        "handles",
        "edges_answer",
        "review",
    ]
    assert data["edges_answer"] == question.key
    justifies = data["edges"][1]
    assert justifies["source"] == found.corrected.cut.groups[0].id
    assert justifies["provenance"] == {
        "by": "agent",
        "model": "fake/model",
        "question": question.key,
        "detail": {"stage": "edges", "template": question.template.version},
    }
    assert data["review"]["stages"] == ["groups", "edges"]
    assert data["review"]["questions"] == {"groups": 1, "edges": 1}


def test_a_replay_gives_the_same_edges_and_the_same_bytes(paper, tmp_path) -> None:
    store = tmp_path / "store"
    found, payload = review(paper, store, edges_host(), settings=EDGES_SETTINGS)

    again, replayed = review(paper, store, settings=EDGES_SETTINGS)

    assert again.edges == found.edges and again.complete
    assert replayed == payload


def test_a_run_stopped_before_the_edges_asks_nothing_of_them(paper, tmp_path) -> None:
    fake = edges_host(cost=0.006)

    found, payload = review(
        paper, tmp_path / "store", fake, budget=0.001, settings=EDGES_SETTINGS
    )

    assert found.stopped is not None
    assert not any(EDGES_FILE in w.files for w in fake.workspaces)
    (question,) = found.questions[Stage.EDGES]
    assert question.key in found.unanswered and not found.edges
    assert "edges" not in json.loads(payload)


def test_without_the_edges_stage_the_payload_is_as_before(paper, tmp_path) -> None:
    settings = replace(EDGES_SETTINGS, stages=(Stage.GROUPS,))

    found, payload = review(paper, tmp_path / "store", edges_host(), settings=settings)

    assert Stage.EDGES not in found.questions
    assert "edges" not in json.loads(payload)


# --- the edges' later passes ------------------------------------------

DONE = re.compile(r"^⟦done \S+⟧$", re.MULTILINE)


def review_edges(drop: bool):
    """The edges session: a first pass as `write_edges`; a review (its sheet
    already has edges) changes nothing, or with `drop` removes the proof's
    uses edge."""

    def edit(text: str) -> str:
        if "→ justifies" not in text:
            return write_edges(text)
        if drop:
            text = "\n".join(
                line for line in text.split("\n") if not line.startswith("→ uses")
            )
        return text

    return edit


def passes_host(drop: bool) -> FakeHost:
    return FakeHost(
        answer, edits={GROUPS_FILE: regroup, EDGES_FILE: review_edges(drop)}
    )


def edges_sheets(fake: FakeHost) -> list[str]:
    return [w.files[EDGES_FILE] for w in fake.workspaces if EDGES_FILE in w.files]


def test_a_later_edges_pass_reviews_the_sheet_the_first_left(paper, tmp_path) -> None:
    fake = passes_host(drop=True)
    settings = replace(EDGES_SETTINGS, edge_passes=2)

    found, payload = review(paper, tmp_path / "store", fake, settings=settings)

    assert found.complete
    first, second = found.questions[Stage.EDGES]
    sheets = edges_sheets(fake)
    assert len(sheets) == 2
    assert DONE.search(sheets[0]) is None and "→" not in sheets[0]
    # The review is shown the first pass's sheet, its edges and done marks.
    assert "→ uses def:gain" in sheets[1] and DONE.search(sheets[1])
    review_workspace = fake.workspaces[-1]
    # the writer's brief, without the first pass's section on lines.md
    assert review_workspace.instructions.startswith(
        first.template.render() + "\n\n## This pass: a review"
    )
    assert second.template.name == "edges-review" and second.key != first.key
    # The last answered pass's records are the edge half.
    assert [e.kind.value for e in found.edges.edges] == ["justifies"]
    assert found.edges_answer is not None and found.edges_answer.key == second.key
    data = json.loads(payload)
    assert data["review"]["edge_passes"] == 2
    assert data["review"]["questions"]["edges"] == 2
    assert data["review"]["templates"]["edges-file"] == first.template.version
    assert data["review"]["templates"]["edges-review"] == second.template.version
    # the edge half is the review's; its line the first pass wrote
    assert data["edges_answer"] == second.key
    assert {e["provenance"]["question"] for e in data["edges"]} == {first.key}


def test_the_edges_passes_stop_when_one_changes_nothing(paper, tmp_path) -> None:
    fake = passes_host(drop=False)
    settings = replace(EDGES_SETTINGS, edge_passes=3)

    found, _ = review(paper, tmp_path / "store", fake, settings=settings)

    assert found.complete and len(found.questions[Stage.EDGES]) == 2
    assert len(edges_sheets(fake)) == 2
    assert [e.kind.value for e in found.edges.edges] == ["uses", "justifies"]


def test_one_store_replays_with_and_without_the_later_pass(paper, tmp_path) -> None:
    store = tmp_path / "store"
    two = replace(EDGES_SETTINGS, edge_passes=2)
    found, payload = review(paper, store, passes_host(drop=True), settings=two)
    _, first_only = review(
        paper, tmp_path / "first", passes_host(drop=True), settings=EDGES_SETTINGS
    )

    again, replayed = review(paper, store, settings=two)
    one, replayed_one = review(paper, store, settings=EDGES_SETTINGS)

    assert again.edges == found.edges and replayed == payload
    # The first pass's key is the same under one pass or two.
    assert one.questions[Stage.EDGES][0].key == found.questions[Stage.EDGES][0].key
    assert [e.kind.value for e in one.edges.edges] == ["uses", "justifies"]
    assert json.loads(replayed_one)["review"]["edge_passes"] == 1
    # A one-pass run in a store of its own writes the same bytes.
    assert first_only == replayed_one


def test_a_later_pass_waits_for_the_first(paper, tmp_path) -> None:
    """A first pass refused every time leaves no sheet to review: the later
    pass is not built."""
    fake = FakeHost(answer, edits={GROUPS_FILE: regroup, EDGES_FILE: lambda t: t})
    settings = replace(EDGES_SETTINGS, edge_passes=2)

    found, payload = review(paper, tmp_path / "store", fake, settings=settings)

    (question,) = found.questions[Stage.EDGES]
    assert question.key in found.unanswered and not found.edges
    assert "edges" not in json.loads(payload)


# --- a run's working state through the edges passes ----------------


def run_review(paper, directory: Path, host=None, settings=EDGES_SETTINGS):
    """A review under a run file, as a command runs it: the run's working
    state opened on the store, the resolver holding each text in it."""
    store = RecordStore(directory)
    run = RunFile(directory, "build", "v", "made")
    runner = None
    if host is not None:
        runner = Runner(host, Resolver(store, run=run), 10.0, FLAT, jobs=1)
    return review_cut(paper.document, paper.cut, store, settings, runner, run), run


@pytest.mark.usefixtures("develop_mode")
def test_a_run_holds_each_edges_sheet_and_its_cursor_follows(paper, tmp_path) -> None:
    store = tmp_path / "store"
    settings = replace(EDGES_SETTINGS, edge_passes=2)

    found, run = run_review(paper, store, passes_host(drop=True), settings)

    first, second = found.questions[Stage.EDGES]
    held = run_state.read(store, "build", "v", "made")
    assert held is not None and set(held.texts) == {first.key, second.key}
    answers = RecordStore(store).authoritative()
    assert held.texts[second.key] == record_file.text(store, answers[second.key])
    assert (held.cursor.stage, held.cursor.step) == ("edges", 2)
    assert held.cursor.edges_answer == second.key
    # no other stage's text is held
    assert len(held.texts) == 2


def test_a_later_pass_is_shown_the_sheet_the_run_holds(paper, tmp_path) -> None:
    """The run's text is what a later pass stands on, not the store's."""
    store = tmp_path / "store"
    found, run = run_review(paper, store, passes_host(drop=True))
    (first,) = found.questions[Stage.EDGES]
    marked = run.text(first.key).replace("\n", "\n% held by the run\n", 1)
    run_state.write(
        store, run_state.RunState("build", run.state.cursor, {first.key: marked})
    )
    fake = passes_host(drop=True)

    run_review(paper, store, fake, replace(EDGES_SETTINGS, edge_passes=2))

    (sheet,) = edges_sheets(fake)
    assert "% held by the run" in sheet


def test_a_run_stopped_before_filing_asks_again(paper, tmp_path, monkeypatch) -> None:
    """The text is held, the answer never filed: the text stands on no
    answer, and the next run asks the question."""
    store = tmp_path / "store"

    def lost(*args, **kwargs):
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        held = RecordStore.append

        def append(self, answer, *args, **kwargs):
            if answer.stage == Stage.EDGES.value:
                lost()
            return held(self, answer, *args, **kwargs)

        patched.setattr(RecordStore, "append", append)
        with pytest.raises(KeyboardInterrupt):
            run_review(paper, store, passes_host(drop=True))
    stopped = run_state.read(store, "build", "v", "made")
    assert stopped is not None and len(stopped.texts) == 1
    assert stopped.cursor.edges_answer is None
    fake = passes_host(drop=True)

    found, _ = run_review(paper, store, fake)

    assert found.complete and len(edges_sheets(fake)) == 1


def test_a_run_stopped_after_filing_resumes_without_asking(
    paper, tmp_path, monkeypatch
) -> None:
    """Filed, the cursor not moved: the next run applies the answer from
    the text it holds, and moves the cursor."""
    store = tmp_path / "store"
    with monkeypatch.context() as patched:

        def stopped(self, **cursor):
            raise KeyboardInterrupt

        patched.setattr(RunFile, "moved", stopped)
        with pytest.raises(KeyboardInterrupt):
            run_review(paper, store, passes_host(drop=True))
    fake = passes_host(drop=True)

    found, run = run_review(paper, store, fake)

    assert found.complete and edges_sheets(fake) == []
    assert run.state.cursor.edges_answer == found.edges_answer.key


def test_a_run_stopped_after_its_cursor_resumes_where_it_stood(paper, tmp_path) -> None:
    store = tmp_path / "store"
    found, _ = run_review(paper, store, passes_host(drop=True))
    fake = passes_host(drop=True)

    again, run = run_review(paper, store, fake)

    assert edges_sheets(fake) == [] and again.edges == found.edges
    assert run.state.cursor.edges_answer == found.edges_answer.key


# --- the whole build --------------------------------------------------

BUILD = (Stage.BOUNDARIES, Stage.ROLES, Stage.GROUPS, Stage.EDGES)
BUILD_SETTINGS = ReviewSettings(
    chunk=None, overlap=0, stages=BUILD, files=("extra.tex", "main.tex")
)


def no_claims(text: str) -> str:
    """The roles session's edit: every block a definition, so the edges
    session needs no standing."""
    return re.sub(r"^⟦(b\d+)⟧", r"⟦\1 definition⟧", text, flags=re.MULTILINE)


def build_host(**options) -> FakeHost:
    """Each stage's session: the cut as `edit`, the roles as `no_claims`,
    the groups as `regroup`, the edges every block done and no edge."""
    return FakeHost(
        answer,
        edits={
            "cut.md": edit,
            "roles.md": no_claims,
            GROUPS_FILE: regroup,
            EDGES_FILE: lambda text: written(text, {}, []),
        },
        **options,
    )


def test_a_build_asks_every_stage_in_order(paper, tmp_path) -> None:
    fake = build_host()

    found, payload = review(paper, tmp_path / "store", fake, settings=BUILD_SETTINGS)

    assert found.complete, found.unanswered
    assert [stage_file(w) for w in fake.workspaces][-3:] == [
        "roles.md",
        GROUPS_FILE,
        LINES_FILE,
    ]
    assert list(found.questions) == list(BUILD)
    data = json.loads(payload)
    assert data["review"]["stages"] == [s.value for s in BUILD]
    assert found.edges_answer is not None and data["edges"] == []


def test_a_stopped_build_resumes_where_it_stopped(paper, tmp_path) -> None:
    store = tmp_path / "store"
    stopped, _ = review(
        paper, store, build_host(cost=0.006), budget=0.013, settings=BUILD_SETTINGS
    )
    assert stopped.stopped is not None and stopped.edges_answer is None
    asked_before = {q.key for qs in stopped.questions.values() for q in qs} - set(
        stopped.unanswered
    )

    fake = build_host(cost=0.006)
    resumed, payload = review(paper, store, fake, settings=BUILD_SETTINGS)

    assert resumed.complete and resumed.edges_answer is not None
    asked_now = {k for r in resumed.reports for k in r.asked}
    assert asked_now and not asked_now & asked_before
    _, replayed = review(paper, store, settings=BUILD_SETTINGS)
    assert replayed == payload


# --- cut corrections the edges propose -----------------------------------

SPLIT_LINE = '✂ split "Hence the output" · "Hence the output is bounded." — two claims'


def proposing(text: str) -> str:
    """The edges as `write_edges`, proposing a split of the proof's first
    block where its claim starts."""
    lines = {**WRITER}
    lines["by-definition-def:gain-the"] = [
        *WRITER["by-definition-def:gain-the"],
        SPLIT_LINE,
    ]
    return written(text, lines, ["Pervasive: setting"])


def proposing_host(**options) -> FakeHost:
    return FakeHost(
        answer, edits={GROUPS_FILE: regroup, EDGES_FILE: proposing}, **options
    )


def test_without_a_round_a_proposal_is_listed_and_not_applied(paper, tmp_path) -> None:
    store = tmp_path / "store"
    found, payload = review(paper, store, proposing_host(), settings=EDGES_SETTINGS)

    assert found.complete
    (proposal,) = found.proposals
    source = paper.id_at("\\begin{proof}")
    assert proposal.correction.block == source and not proposal.applied
    assert (proposal.round, proposal.stage, proposal.problem) == (1, "edges", NO_ROUND)
    assert proposal.correction.evidence == ("Hence the output is bounded.",)
    # Recorded, and the cut left as it was.
    assert store_corrections(store)[-1] == proposal.correction
    assert source in [b.id for b in found.corrected.cut.blocks]
    assert paper.id_at("Hence the output") not in [
        b.id for b in found.corrected.cut.blocks
    ]
    (entry,) = json.loads(payload)["review"]["cut_corrections"]
    assert entry["id"] == proposal.correction.id
    assert {k: entry[k] for k in ("round", "op", "stage", "block", "applied")} == {
        "round": 1,
        "op": "split",
        "stage": "edges",
        "block": source,
        "applied": False,
    }
    assert entry["problem"] == NO_ROUND and entry["why"] == "two claims"
    (at,) = entry["at"]
    (evidence,) = entry["evidence"]
    assert at["text"] == "Hence the output" and at["at"]["file"] == "main.tex"
    assert evidence["at"]["start"] == at["at"]["start"]
    (before,) = entry["changed"]["before"]
    assert before["id"] == source and before["owned"]
    assert entry["changed"]["after"] == []

    again, replayed = review(paper, store, settings=EDGES_SETTINGS)
    assert again.proposals == found.proposals and replayed == payload


def test_without_a_proposal_the_export_has_no_cut_corrections(paper, tmp_path) -> None:
    found, payload = review(
        paper, tmp_path / "store", edges_host(), settings=EDGES_SETTINGS
    )

    assert found.proposals == ()
    assert "cut_corrections" not in json.loads(payload)["review"]


def store_corrections(directory: Path) -> list:
    return list(RecordStore(directory).corrections())


# --- the rounds --------------------------------------------------------

HEAD = "by-definition-def:gain-the"
TAIL = "hence-the-output-is"


def finished(text: str) -> str:
    """The re-ask's edit: every reopened block and group marked done."""
    return done(text)


def round_host(reask=finished, first=proposing, **options) -> FakeHost:
    """Groups as `regroup`; the edges' first pass as `first`, their re-ask
    (its sheet already holds edges) as `reask`; the roles of the blocks a
    correction changed as `no_claims`."""

    def edges(text: str) -> str:
        return reask(text) if "→ " in text else first(text)

    return FakeHost(
        answer,
        edits={GROUPS_FILE: regroup, EDGES_FILE: edges, "roles.md": no_claims},
        **options,
    )


ROUND = replace(EDGES_SETTINGS, cut_rounds=1)


def test_a_round_applies_a_split_and_asks_again_what_it_touched(
    paper, tmp_path
) -> None:
    store = tmp_path / "store"
    fake = round_host()

    found, payload = review(paper, store, fake, settings=ROUND)

    assert found.complete, found.unanswered
    head, tail = paper.id_at("\\begin{proof}"), paper.id_at("Hence the output")
    (proposal,) = found.proposals
    assert proposal.applied and proposal.problem is None
    assert (proposal.round, proposal.after) == (1, (head, tail))
    cut = found.corrected.cut
    assert {head, tail} <= {b.id for b in cut.blocks}
    assert [r.value for r in cut.block(tail).roles] == ["definition"]
    (done_round,) = found.cut_rounds
    assert done_round.touched == (head, tail)
    assert done_round.reopened == (HEAD, TAIL)
    # Asked: the groups, the edges, the roles of the two blocks, the re-ask.
    assert fake.calls == 4
    (roles,) = found.questions[Stage.ROLES]
    assert [h for h in roles.handles if h.startswith("b")] == ["b1", "b2"]
    first, again = found.questions[Stage.EDGES]
    reask_sheet = fake.workspaces[-1].files[EDGES_FILE]
    assert "⟦done thm:bound⟧" in reask_sheet and f"⟦done {HEAD}⟧" not in reask_sheet
    assert found.edges_answer is not None and found.edges_answer.key == again.key
    assert again.input_cut == cut
    assert [e.kind.value for e in found.edges.edges] == ["uses", "justifies"]
    data = json.loads(payload)
    (entry,) = data["review"]["cut_corrections"]
    assert entry["applied"] and entry["problem"] is None
    assert entry["changed"]["after"] == [head, tail]
    assert data["edges_answer"] == again.key
    # the re-ask wrote no edge anew (it marked its owners done): every
    # line is the first pass's
    assert {e["provenance"]["question"] for e in data["edges"]} == {first.key}

    replayed, replay = review(paper, store, settings=ROUND)
    assert replay == payload and replayed.cut_rounds == found.cut_rounds


def test_a_round_s_re_ask_is_held_by_the_run_and_its_cursor_follows(
    paper, tmp_path
) -> None:
    store = tmp_path / "store"

    found, run = run_review(paper, store, round_host(), ROUND)

    first, again = found.questions[Stage.EDGES]
    held = run_state.read(store, "build", "v", "made")
    assert held is not None and set(held.texts) == {first.key, again.key}
    assert (held.cursor.stage, held.cursor.step) == ("cut-rounds", 1)
    assert held.cursor.edges_answer == again.key


def test_a_round_stands_on_the_sheet_the_run_holds(paper, tmp_path) -> None:
    """The re-ask is carried from the run's text of the first pass, not
    the store's: a line the run's text lacks is not carried."""
    store = tmp_path / "store"
    found, run = run_review(paper, store, round_host())
    (first,) = found.questions[Stage.EDGES]
    line = '→ uses def:gain ref · "By Definition~\\ref{def:gain}"'
    assert line in run.text(first.key)
    plain = round_host()
    run_review(paper, tmp_path / "plain", plain, ROUND)
    assert "uses def:gain" in plain.workspaces[-1].files[EDGES_FILE]
    marked = run.text(first.key).replace(line + "\n", "", 1)
    run_state.write(
        store, run_state.RunState("build", run.state.cursor, {first.key: marked})
    )
    fake = round_host()

    run_review(paper, store, fake, ROUND)

    assert "uses def:gain" not in fake.workspaces[-1].files[EDGES_FILE]


def test_a_round_stopped_after_filing_resumes_without_asking(
    paper, tmp_path, monkeypatch
) -> None:
    store = tmp_path / "store"
    moved = RunFile.moved

    def stopped(self, **cursor):
        if cursor.get("stage") == "cut-rounds":
            raise KeyboardInterrupt
        moved(self, **cursor)

    with monkeypatch.context() as patched:
        patched.setattr(RunFile, "moved", stopped)
        with pytest.raises(KeyboardInterrupt):
            run_review(paper, store, round_host(), ROUND)
    fake = round_host()

    found, run = run_review(paper, store, fake, ROUND)

    assert found.complete and fake.calls == 0
    assert run.state.cursor.edges_answer == found.edges_answer.key


def test_cut_rounds_zero_replays_the_round_s_store_as_its_first_pass(
    paper, tmp_path
) -> None:
    """One store, two exports: without a round, the
    store's first pass is the edge half and its proposal is not applied."""
    store = tmp_path / "store"
    review(paper, store, round_host(), settings=ROUND)
    _, alone = review(
        paper, tmp_path / "alone", proposing_host(), settings=EDGES_SETTINGS
    )

    without, payload = review(paper, store, settings=EDGES_SETTINGS)

    assert without.cut_rounds == () and not without.proposals[0].applied
    assert payload == alone


def test_a_proposal_with_evidence_reverses_an_earlier_one_once(paper, tmp_path) -> None:
    """A boundary changed once may be changed back by a
    correction that carries evidence, as an edges' cut line does."""
    undo = f'✂ merge {TAIL} · "By Definition" — one paragraph after all'

    def undoing(text: str) -> str:
        if f"⟦{TAIL} " not in text:
            return finished(text)
        return finished(text).replace(f"⟦done {HEAD}⟧", f"{undo}\n⟦done {HEAD}⟧")

    found, payload = review(
        paper,
        tmp_path / "store",
        round_host(reask=undoing),
        settings=replace(EDGES_SETTINGS, cut_rounds=2),
    )

    assert found.complete, found.unanswered
    first, second = found.proposals
    assert first.applied and first.reverses == ()
    assert (second.round, second.applied) == (2, True)
    assert second.reverses == (paper.id_at("Hence the output"),)
    blocks = {b.id for b in found.corrected.cut.blocks}
    assert paper.id_at("Hence the output") not in blocks
    entries = json.loads(payload)["review"]["cut_corrections"]
    assert [e["round"] for e in entries] == [1, 2]
    assert "reverses" not in entries[0]
    assert entries[1]["reverses"] == [paper.id_at("Hence the output")]
    assert _round_counts(found) == [
        {"round": 1, "proposed": 1, "applied": 1, "dropped": 0, "reopened": 2},
        {"round": 2, "proposed": 1, "applied": 1, "dropped": 0, "reopened": 1},
    ]


def test_a_boundary_is_never_changed_a_third_time(paper, tmp_path) -> None:
    undo = f'✂ merge {TAIL} · "By Definition" — one paragraph after all'

    def flipping(text: str) -> str:
        if f"⟦{TAIL} " in text:
            return finished(text).replace(f"⟦done {HEAD}⟧", f"{undo}\n⟦done {HEAD}⟧")
        return finished(text).replace(f"⟦done {HEAD}⟧", f"{SPLIT_LINE}\n⟦done {HEAD}⟧")

    found, _ = review(
        paper,
        tmp_path / "store",
        round_host(reask=flipping),
        settings=replace(EDGES_SETTINGS, cut_rounds=3),
    )

    assert found.complete, found.unanswered
    split, merge, again = found.proposals
    assert split.applied and merge.applied and not again.applied
    assert again.problem == DROPPED_TWICE + paper.id_at("Hence the output")
    assert paper.id_at("Hence the output") not in {
        b.id for b in found.corrected.cut.blocks
    }


def test_a_refused_proposal_asks_nothing_again(paper, tmp_path) -> None:
    """A footnote merged with the block after the one it lies in: the
    sheet's check lets it through, the assembler refuses two nesting
    levels, so the round applies nothing and asks nothing."""

    def across(text: str) -> str:
        lines = {
            **WRITER,
            "small-means-below-one": [
                '✂ merge next-the-bound-is · "Small means below one." — one note'
            ],
        }
        return written(text, lines, ["Pervasive: setting"])

    fake = round_host(first=across)

    found, _ = review(paper, tmp_path / "store", fake, settings=ROUND)

    (proposal,) = found.proposals
    assert not proposal.applied and "two nesting levels" in proposal.problem
    assert fake.calls == 2 and found.cut_rounds == ()
    assert Stage.ROLES not in found.questions


def test_an_unanswered_reask_takes_its_round_back(paper, tmp_path) -> None:
    """The export is the one before the round plus a failed round: the first pass's edge
    half on the cut before it, the round's proposal not applied; the same command again
    resumes it."""
    store = tmp_path / "store"
    fake = round_host(reask=lambda text: text)

    found, payload = review(paper, store, fake, settings=ROUND)
    _, without = review(paper, store, settings=EDGES_SETTINGS)

    first, again = found.questions[Stage.EDGES]
    assert not found.complete and again.key in found.unanswered
    assert found.edges_answer is not None and found.edges_answer.key == first.key
    (proposal,) = found.proposals
    assert (proposal.applied, proposal.problem) == (False, EDGES_UNANSWERED)
    assert found.cut_rounds == ()
    assert paper.id_at("Hence the output") not in {
        b.id for b in found.corrected.cut.blocks
    }
    assert_round_failed(payload, without, EDGES_UNANSWERED)

    resumed, _ = review(paper, store, round_host(), settings=ROUND)
    assert resumed.complete and len(resumed.cut_rounds) == 1


def test_an_unanswered_roles_session_takes_its_round_back(paper, tmp_path) -> None:
    store = tmp_path / "store"
    fake = round_host()
    fake.edits["roles.md"] = lambda text: text  # never a role: refused

    found, payload = review(paper, store, fake, settings=ROUND)
    _, without = review(paper, store, settings=EDGES_SETTINGS)

    (roles,) = found.questions[Stage.ROLES]
    assert roles.key in found.unanswered and len(found.questions[Stage.EDGES]) == 1
    (proposal,) = found.proposals
    assert (proposal.applied, proposal.problem) == (False, ROLES_UNANSWERED)
    assert found.cut_rounds == ()
    assert_round_failed(payload, without, ROLES_UNANSWERED)


def assert_round_failed(payload: str, without: str, problem: str) -> None:
    """A round taken back: the export is the one without the round, but for
    the round's setting, its questions, and its proposal's problem."""
    data, before = json.loads(payload), json.loads(without)
    review, earlier = data.pop("review"), before.pop("review")
    assert data == before
    (entry,) = review["cut_corrections"]
    assert (entry["applied"], entry["problem"], entry["changed"]["after"]) == (
        False,
        problem,
        [],
    )
    assert {k for k in review if review[k] != earlier.get(k)} <= {
        "cut_rounds",
        "cut_corrections",
        "questions",
        "templates",
        "unanswered",
        "complete",
    }


def test_a_second_round_applies_what_the_first_re_ask_proposed(paper, tmp_path) -> None:
    second = '✂ split "the output is bounded." · "the output is bounded." — again'

    def splitting(text: str) -> str:
        if "⟦the-output-is-bounded" in text:
            # The split cut through the proof's evidence: the group, reopened,
            # quotes it again within one block.
            return finished(text).replace(
                '· "Hence the output is bounded."', '· "the output is bounded."'
            )
        tail = f"⟦{TAIL} definition⟧ main.tex:17:1 (prose)\n"
        return finished(text).replace(
            f"{tail}Hence the output is bounded.\n",
            f"{tail}Hence the output is bounded.\n{second}\n",
        )

    fake = round_host(reask=splitting)
    settings = replace(EDGES_SETTINGS, cut_rounds=2)

    found, payload = review(paper, tmp_path / "store", fake, settings=settings)

    assert found.complete, found.unanswered
    assert [(p.round, p.applied) for p in found.proposals] == [(1, True), (2, True)]
    assert [r.number for r in found.cut_rounds] == [1, 2]
    last = paper.id_at("the output is bounded.")
    assert last in {b.id for b in found.corrected.cut.blocks}
    assert found.cut_rounds[1].touched == (paper.id_at("Hence the output"), last)
    assert PROOF in found.cut_rounds[1].reopened
    # Groups, edges, then each round's roles and re-ask.
    assert fake.calls == 6
    assert json.loads(payload)["review"]["cut_rounds"] == 2


def test_without_a_round_the_export_is_a_build_without_proposals(
    paper, tmp_path
) -> None:
    _, proposed = review(
        paper, tmp_path / "a", proposing_host(), settings=EDGES_SETTINGS
    )
    _, plain = review(paper, tmp_path / "b", edges_host(), settings=EDGES_SETTINGS)

    # The resolver numbers an answer's corrections before its edge records,
    # so the edges' ids move by the proposal's; nothing else differs.
    data, other = json.loads(proposed), json.loads(plain)
    del data["review"]["cut_corrections"]
    for payload in (data, other):
        for edge in payload["edges"]:
            del edge["id"]
    assert data == other


def test_a_store_without_proposals_replays_the_same_under_any_round(
    paper, tmp_path
) -> None:
    """A store whose edges propose nothing: a round asks nothing, and the
    export differs only in its setting."""
    store = tmp_path / "store"
    found, payload = review(paper, store, edges_host(), settings=EDGES_SETTINGS)

    again, replayed = review(paper, store, settings=ROUND)

    assert again.complete and again.edges == found.edges and again.proposals == ()
    data = json.loads(replayed)
    assert data["review"].pop("cut_rounds") == 1
    before = json.loads(payload)
    assert before["review"].pop("cut_rounds") == 0
    assert data == before


# --- flags -------------------------------------------------------------

DEFINITION_MARK = "⟦b3 definition⟧"


def flagging_host(**options) -> FakeHost:
    """A build whose roles, groups and edges sessions each flag the
    definition."""

    def roles(text: str) -> str:
        return no_claims(text).replace(
            DEFINITION_MARK,
            f'{DEFINITION_MARK}\n⟦? role-doubt "The gain is" — a ratio, or a claim⟧',
        )

    def groups(text: str) -> str:
        return regroup(text).replace(
            DEFINITION_MARK, f'{DEFINITION_MARK}\n⟦? "ratio of output" — friction⟧'
        )

    def edges(text: str) -> str:
        return written(
            text, {"def:gain": ['? role "output to input" — read as a claim']}, []
        )

    return FakeHost(
        answer,
        edits={
            "cut.md": edit,
            "roles.md": roles,
            GROUPS_FILE: groups,
            EDGES_FILE: edges,
        },
        **options,
    )


def test_a_build_exports_the_flags_each_stage_raised(paper, tmp_path) -> None:
    store = tmp_path / "store"
    found, payload = review(paper, store, flagging_host(), settings=BUILD_SETTINGS)

    assert found.complete, found.unanswered
    definition = paper.id_at("\\begin{definition}")
    assert [(r.stage, r.flag.signal.value, r.flag.blocks) for r in found.flags] == [
        ("roles", "role-doubt", (definition,)),
        ("groups", "other", (definition,)),
        ("edges", "role", (definition,)),
    ]
    assert all(r.by == "agent" for r in found.flags)
    flags = json.loads(payload)["review"]["flags"]
    assert [f["id"] for f in flags] == [r.flag.id for r in found.flags]
    assert [(b["id"], b["handle"]) for b in flags[2]["blocks"]] == [
        (definition, "def:gain")
    ]
    assert flags[2]["evidence"][0]["at"]["file"] == "main.tex"
    assert {f["decided"] for f in flags} == {None}

    again, replayed = review(paper, store, settings=BUILD_SETTINGS)
    assert replayed == payload


def test_a_build_without_flags_exports_an_empty_list(paper, tmp_path) -> None:
    _, payload = review(
        paper, tmp_path / "store", build_host(), settings=BUILD_SETTINGS
    )

    assert json.loads(payload)["review"]["flags"] == []


# --- weighing ------------------------------------------------------


def made(cid, op, answer="a", evidence=(), decision=None, **fields) -> CutCorrection:
    return CutCorrection(
        cid,
        op,
        CorrectionClass.OTHER,
        "w",
        answer,
        evidence=evidence,
        decision=decision,
        **fields,
    )


def test_a_reversal_without_evidence_is_dropped_and_with_it_applies(paper) -> None:
    head, tail = paper.id_at("\\begin{proof}"), paper.id_at("Hence the output")
    split = made("s1", CutOp.SPLIT, block=head, at=("Hence the output",))
    after = corrected_cut(paper.document, paper.cut, [split]).cut

    def weighed(evidence):
        merge = made("m1", CutOp.MERGE, "b", evidence, blocks=(head, tail))
        return weigh(paper.document, paper.cut, [split], after, [merge], 1, "edges")

    (bare,) = weighed(())
    (shown,) = weighed(("By Definition",))

    assert not bare.applied and bare.problem == DROPPED + tail
    assert shown.applied and shown.reverses == (tail,)


def test_a_decision_applies_whole_or_not_at_all(paper) -> None:
    group = paper.cut.groups[0].id
    first, second = paper.cut.blocks[0].id, paper.cut.blocks[1].id
    setting, closing = paper.cut.blocks[0].id, paper.cut.blocks[8].id
    ungroup = made("u1", CutOp.UNGROUP, decision="u1", group=group)

    def weighed(*blocks):
        merge = made("m1", CutOp.MERGE, decision="u1", evidence=("x",), blocks=blocks)
        return weigh(
            paper.document, paper.cut, [], paper.cut, [ungroup, merge], 1, "decide"
        )

    applied = weighed(first, second)
    refused = weighed(setting, closing)  # not adjacent: the merge fails

    assert [p.applied for p in applied] == [True, True]
    assert [p.applied for p in refused] == [False, False]
    assert refused[0].problem == refused[1].problem and refused[0].problem


# --- the deciding step ------------------------------------

DECIDE_SETTINGS = replace(BUILD_SETTINGS, decide_passes=1)
SECOND_SPLIT = "⟦new — the setting's second sentence is its own⟧\n" + SECOND_AT


def deciding(text: str) -> str:
    """The deciding step splits the setting's second sentence off; the
    boundary pass as `edit`. The deciding step's file shows part lines."""
    if "⟦+ " in text or "⟦group " in text:
        return text.replace(SECOND_AT, SECOND_SPLIT, 1)
    return edit(text)


def decide_host(first=lambda text: written(text, {}, []), **options) -> FakeHost:
    """A build as `build_host`, with the deciding step as `deciding`; the
    edges' first session as `first`, their re-ask as `finished`."""

    def edges(text: str) -> str:
        return finished(text) if "⟦done " in text else first(text)

    return FakeHost(
        answer,
        edits={
            "cut.md": deciding,
            "roles.md": no_claims,
            GROUPS_FILE: regroup,
            EDGES_FILE: edges,
        },
        **options,
    )


def test_the_deciding_step_corrects_the_cut_before_the_edges(paper, tmp_path) -> None:
    store = tmp_path / "store"
    fake = decide_host()

    found, payload = review(paper, store, fake, settings=DECIDE_SETTINGS)

    assert found.complete, found.unanswered
    (decided,) = found.questions[Stage.DECIDE]
    (split,) = found.proposals
    assert (split.stage, split.round, split.applied) == ("decide", 0, True)
    assert split.correction.evidence == split.correction.at
    assert split.correction.why == "the setting's second sentence is its own"
    second = paper.id_at(SECOND_AT)
    assert second in {b.id for b in found.corrected.cut.blocks}
    # The roles of the blocks it changed, asked alone; the groups not again,
    # the change lying far from the proof; the edges asked whole, once.
    first_roles, touched = found.questions[Stage.ROLES]
    assert set(touched.handles.values()) >= {paper.id_at("We fix a field"), second}
    assert len(found.questions[Stage.GROUPS]) == 1
    (edges,) = found.questions[Stage.EDGES]
    assert edges.input_cut == found.corrected.cut
    assert found.edges_carried_from is None
    data = json.loads(payload)["review"]
    assert data["decide_passes"] == 1 and "edges_carried_from" not in data
    assert [e["stage"] for e in data["cut_corrections"]] == ["decide"]

    _, replayed = review(paper, store, settings=DECIDE_SETTINGS)
    assert replayed == payload


def test_without_the_deciding_step_a_build_is_as_before(paper, tmp_path) -> None:
    store = tmp_path / "store"
    _, before = review(paper, store, decide_host(), settings=BUILD_SETTINGS)

    data = json.loads(before)["review"]
    assert "decide_passes" not in data and "cut_corrections" not in data


def test_the_deciding_step_carries_the_edges_answered_on_the_cut_before_it(
    paper, tmp_path
) -> None:
    """A store built without the step: its edges are carried onto the
    step's cut and re-asked, its unweighed cut line kept."""
    store = tmp_path / "store"
    cut_line = '✂ split "is sharp." · "Next, the bound is sharp." — a second claim'

    def proposing_first(text: str) -> str:
        return written(text, {"next-the-bound-is": [cut_line]}, [])

    first, _ = review(
        paper, store, decide_host(first=proposing_first), settings=BUILD_SETTINGS
    )
    (earlier,) = first.questions[Stage.EDGES]

    found, payload = review(
        paper, store, decide_host(first=proposing_first), settings=DECIDE_SETTINGS
    )

    assert found.complete, found.unanswered
    (reask,) = found.questions[Stage.EDGES]
    assert reask.prompt == REASK_KICKOFF
    assert found.edges_carried_from == earlier.key
    assert cut_line in reask.workspace.files[EDGES_FILE]
    assert json.loads(payload)["review"]["edges_carried_from"] == earlier.key
    # The carried proposal is weighed by no round: listed, not applied.
    edges_proposal = [p for p in found.proposals if p.stage == "edges"]
    assert [p.problem for p in edges_proposal] == [NO_ROUND]


def test_a_block_in_or_next_to_a_group_asks_the_groups_again(paper) -> None:
    ids = [b.id for b in paper.cut.blocks]  # the proof is blocks 5 to 7

    assert [near_group(paper.cut, [ids[n]]) for n in (0, 4, 5, 7, 8, 9)] == [
        False,
        True,
        True,
        True,
        True,
        False,
    ]


def test_the_deciding_step_sees_the_flags_and_the_export_links_what_it_decided(
    paper, tmp_path
) -> None:
    def flagging_roles(text: str) -> str:
        return no_claims(text).replace(
            "⟦b2 definition⟧",
            '⟦b2 definition⟧\n⟦? split "A second sentence" — two settings⟧',
            1,
        )

    fake = decide_host()
    fake.edits["roles.md"] = flagging_roles

    found, payload = review(paper, tmp_path / "store", fake, settings=DECIDE_SETTINGS)

    assert found.complete, found.unanswered
    (decided,) = found.questions[Stage.DECIDE]
    assert '⟦? roles: split · "A second sentence" — two settings⟧' in (
        shown_file(decided)
    )
    (split,) = found.proposals
    flags = json.loads(payload)["review"]["flags"]
    roles_flag = next(f for f in flags if f["stage"] == "roles")
    assert roles_flag["decided"] == split.correction.id
    assert _decide_counts(found) == {
        "proposed": 1,
        "applied": 1,
        "dropped": 0,
        "reversed": 0,
        "flags_shown": 1,
        "flags_acted_on": 1,
    }


# --- the round takes roles -------------------------------------------

ROLE_FLAG = '? role "The gain is the ratio" — reads as a claim'
GAIN_CLAIM = '⊢ unsupported · "The gain is the ratio of output to input."'


def flagging_edges(text: str) -> str:
    """The edges as `write_edges`, flagging the definition's role."""
    return written(text, {**WRITER, "def:gain": [ROLE_FLAG]}, ["Pervasive: setting"])


def role_host(role: str) -> FakeHost:
    """The edges' first pass as `flagging_edges`; the roles of a flagged
    block given as `role`; the re-ask as `finished`, with the standing a
    claim needs."""

    def roles(text: str) -> str:
        return re.sub(r"^⟦(b\d+)⟧", rf"⟦\1 {role}⟧", text, flags=re.MULTILINE)

    def reask(text: str) -> str:
        return finished(text).replace(
            "⟦done def:gain⟧", f"{GAIN_CLAIM}\n⟦done def:gain⟧"
        )

    fake = round_host(reask=reask, first=flagging_edges)
    fake.edits["roles.md"] = roles
    return fake


def test_a_role_flag_asks_the_roles_and_a_changed_role_reopens_by_type(
    paper, tmp_path
) -> None:
    found, payload = review(
        paper, tmp_path / "store", role_host("claim"), settings=ROUND
    )

    assert found.complete, found.unanswered
    definition = paper.id_at("\\begin{definition}")
    *_, touched = found.questions[Stage.ROLES]
    assert list(touched.handles.values())[:1] == [definition]
    assert "reads as a claim" in touched.prompt
    first, reask = found.questions[Stage.EDGES]
    assert reask.prompt == REASK_KICKOFF
    (round_,) = found.cut_rounds
    assert round_.touched == () and "def:gain" in round_.reopened
    instructions = reask.workspace.instructions
    assert f"roles of {definition}: definition → claim" in instructions
    assert "its roles changed: definition → claim" in instructions
    assert found.proposals == ()
    assert _round_counts(found) == [
        {"round": 1, "proposed": 0, "applied": 0, "dropped": 0, "reopened": 1}
    ]

    _, replayed = review(paper, tmp_path / "store", settings=ROUND)
    assert replayed == payload


def test_a_role_flag_whose_roles_stand_reopens_nothing(paper, tmp_path) -> None:
    found, _ = review(
        paper, tmp_path / "store", role_host("definition"), settings=ROUND
    )

    assert found.complete, found.unanswered
    assert len(found.questions[Stage.EDGES]) == 1
    assert found.cut_rounds == ()
    *_, touched = found.questions[Stage.ROLES]
    assert "reads as a claim" in touched.prompt


def test_without_a_round_a_role_flag_asks_nothing(paper, tmp_path) -> None:
    found, payload = review(
        paper, tmp_path / "store", role_host("claim"), settings=EDGES_SETTINGS
    )

    assert found.complete and len(found.questions[Stage.EDGES]) == 1
    flags = json.loads(payload)["review"]["flags"]
    assert [(f["stage"], f["signal"]) for f in flags if f["by"] == "agent"] == [
        ("edges", "role")
    ]


def test_a_later_edges_pass_is_shown_the_rules_flags_where_asked(
    paper, tmp_path
) -> None:
    using = {
        **WRITER,
        "def:gain": ['→ uses thm:bound · "The gain is the ratio"'],
    }

    def first_or_review(text: str) -> str:
        if "→ " in text:
            return done(text)
        return written(text, using, ["Pervasive: setting"])

    settings = replace(EDGES_SETTINGS, edge_passes=2)
    found = {}
    for shown_rules in (False, True):
        fake = FakeHost(
            answer, edits={GROUPS_FILE: regroup, EDGES_FILE: first_or_review}
        )
        found[shown_rules], _ = review(
            paper,
            tmp_path / f"store-{shown_rules}",
            fake,
            settings=replace(settings, show_rule_flags_in_edge_review=shown_rules),
        )

    for shown_rules, review_ in found.items():
        assert review_.complete, review_.unanswered
        _, second = review_.questions[Stage.EDGES]
        noted = "Positions the rules took" in second.workspace.instructions
        assert noted is shown_rules


def test_the_step_is_taken_back_when_its_roles_go_unanswered(paper, tmp_path) -> None:
    """As a round is, so the edges are asked on the cut
    the groups left and a resumed run asks the step's roles again."""

    def roles(text: str) -> str:
        # The step's roles file holds its changed blocks alone: leave them
        # without a role, so the answer is refused every time.
        return no_claims(text) if "⟦b3⟧" in text else text

    fake = decide_host()
    fake.edits["roles.md"] = roles

    found, payload = review(paper, tmp_path / "store", fake, settings=DECIDE_SETTINGS)

    (split,) = found.proposals
    assert not split.applied and split.problem == STEP_ROLES_UNANSWERED
    assert paper.id_at(SECOND_AT) not in {b.id for b in found.corrected.cut.blocks}
    (edges,) = found.questions[Stage.EDGES]
    assert edges.input_cut == found.corrected.cut
    _, replayed = review(paper, tmp_path / "store", settings=DECIDE_SETTINGS)
    assert replayed == payload


def test_a_step_change_inside_a_group_asks_the_groups_again(paper, tmp_path) -> None:
    def splitting_the_proof(text: str) -> str:
        if "⟦+ " in text or "⟦group " in text:
            return text.replace(
                "By Definition~", "⟦new — the proof's first step⟧\nBy Definition~", 1
            )
        return edit(text)

    fake = decide_host()
    fake.edits["cut.md"] = splitting_the_proof

    found, _ = review(paper, tmp_path / "store", fake, settings=DECIDE_SETTINGS)

    assert found.complete, found.unanswered
    (split,) = found.proposals
    assert split.applied
    first, again = found.questions[Stage.GROUPS]
    assert again.input_cut == found.corrected.cut and first.key != again.key


# --- the edges' role correction ------------------------------------------

CLOSING_CLAIM = '⊢ unsupported · "Closing words of the results."'


def claiming(text: str) -> str:
    """The edges as `write_edges`, with a standing on the closing paragraph,
    which the cutter's cut gives no role."""
    lines = {**WRITER, "closing-words-of-the": [CLOSING_CLAIM]}
    return written(text, lines, ["Pervasive: setting"])


def claiming_host(first=claiming, **options) -> FakeHost:
    return round_host(first=first, **options)


def test_a_standing_on_a_block_with_no_claim_adds_the_role_with_the_answer(
    paper, tmp_path
) -> None:
    """The build applies the role correction with the edges answer: the
    export's cut has the claim, `review.cut_corrections` lists it by the
    edges, the standing is in the edge half, and nothing is asked again,
    whatever the rounds."""
    store = tmp_path / "store"
    fake = claiming_host()

    found, payload = review(paper, store, fake, settings=ROUND)

    assert found.complete, found.unanswered
    closing = paper.id_at("Closing words")
    assert found.corrected.cut.block(closing).roles == (Role.CLAIM,)
    assert found.corrected.cut.block(closing).segments == (Segment("", Role.CLAIM),)
    # asked: the groups and the edges; no touched roles, no re-ask, no round
    assert fake.calls == 2 and Stage.ROLES not in found.questions
    assert len(found.questions[Stage.EDGES]) == 1 and found.cut_rounds == ()
    (proposal,) = found.proposals
    assert proposal.applied and proposal.by == "edges" and proposal.round == 0
    assert proposal.correction.op is CutOp.ROLES
    assert proposal.correction.answer == found.edges_answer.key
    data = json.loads(payload)
    (entry,) = data["review"]["cut_corrections"]
    assert (entry["op"], entry["stage"], entry["by"]) == ("roles", "edges", "edges")
    assert entry["block"] == closing and entry["applied"]
    assert [e["text"] for e in entry["evidence"]] == ["Closing words of the results."]
    assert entry["evidence"][0]["at"] is not None and entry["problem"] is None
    (block,) = [b for b in data["blocks"] if b["id"] == closing]
    assert block["roles"] == ["claim"]
    assert closing in {s["block"] for s in data["standings"]}

    replayed, replay = review(paper, store, settings=ROUND)
    assert replay == payload and replayed.proposals == found.proposals


def test_a_role_correction_is_no_proposal_a_round_weighs(paper, tmp_path) -> None:
    """With a split proposed beside it, the split is weighed and applied in
    round 1 as before; the role correction stands in round 0, unweighed, and
    the re-ask is asked on the cut that holds both."""

    def both(text: str) -> str:
        lines = {
            **WRITER,
            "by-definition-def:gain-the": [
                *WRITER["by-definition-def:gain-the"],
                SPLIT_LINE,
            ],
            "closing-words-of-the": [CLOSING_CLAIM],
        }
        return written(text, lines, ["Pervasive: setting"])

    found, payload = review(
        paper, tmp_path / "store", claiming_host(first=both), settings=ROUND
    )

    assert found.complete, found.unanswered
    roles, split = found.proposals
    assert (roles.correction.op, roles.round, roles.by) == (CutOp.ROLES, 0, "edges")
    assert (split.correction.op, split.round, split.by) == (CutOp.SPLIT, 1, None)
    assert roles.applied and split.applied
    (done_round,) = found.cut_rounds
    closing = paper.id_at("Closing words")
    assert closing not in done_round.touched
    (asked_roles,) = found.questions[Stage.ROLES]
    assert closing not in asked_roles.handles.values()
    _, again = found.questions[Stage.EDGES]
    assert again.input_cut.block(closing).roles == (Role.CLAIM,)
    assert "⟦closing-words-of-the claim⟧" in again.workspace.files[EDGES_FILE]
    _, replayed = review(paper, tmp_path / "store", settings=ROUND)
    assert replayed == payload


def test_a_round_re_ask_s_own_role_correction_applies_in_its_round(
    paper, tmp_path
) -> None:
    """The round's re-ask writes a standing on the new tail block, which the
    round's roles gave a definition: the correction applies in round 1 with
    the re-ask, the claim beside the definition, and asks no
    second round though two are allowed."""

    def claiming_reask(text: str) -> str:
        return finished(text).replace(
            f"⟦done {TAIL}⟧",
            f'⊢ unsupported · "Hence the output is bounded."\n⟦done {TAIL}⟧',
        )

    settings = replace(EDGES_SETTINGS, cut_rounds=2)
    found, payload = review(
        paper, tmp_path / "store", round_host(reask=claiming_reask), settings=settings
    )

    assert found.complete, found.unanswered
    split, roles = found.proposals
    assert (split.correction.op, split.round, split.applied) == (CutOp.SPLIT, 1, True)
    assert (roles.correction.op, roles.round, roles.by) == (CutOp.ROLES, 1, "edges")
    assert roles.applied and roles.correction.answer == found.edges_answer.key
    tail = paper.id_at("Hence the output")
    assert found.corrected.cut.block(tail).roles == (Role.DEFINITION, Role.CLAIM)
    assert len(found.cut_rounds) == 1 and len(found.questions[Stage.EDGES]) == 2
    _, replayed = review(paper, tmp_path / "store", settings=settings)
    assert replayed == payload


def test_an_unplaceable_claim_part_is_a_role_flag_a_round_sends_to_roles(
    paper, tmp_path, monkeypatch
) -> None:
    """A part that cannot be placed: the edges answer stands, its claim
    beside the block's roles, with a `role` flag, stored and
    exported in `review.flags`; without a round it stays a flag, and with
    one the round asks the block's roles with its note."""

    def unplaceable(*args):
        raise ValueError("no excerpt stands once")

    monkeypatch.setattr("axiomation.questions.edges_file.claim_part", unplaceable)
    closing = paper.id_at("Closing words")
    found = {}
    for rounds in (0, 1):
        settings = replace(EDGES_SETTINGS, cut_rounds=rounds)
        found[rounds], payload = review(
            paper, tmp_path / f"store-{rounds}", claiming_host(), settings=settings
        )
        assert found[rounds].complete, found[rounds].unanswered
        (stored,) = RecordStore(tmp_path / f"store-{rounds}").flags_of(
            found[rounds].questions[Stage.EDGES][0].key
        )
        assert (stored.signal.value, stored.blocks) == ("role", (closing,))
        flags = json.loads(payload)["review"]["flags"]
        assert [(f["stage"], f["signal"]) for f in flags if f["by"] == "agent"] == [
            ("edges", "role")
        ]
        assert "no excerpt stands once" in stored.note
        (claimed,) = [p for p in found[rounds].proposals if p.round == 0]
        assert claimed.applied and claimed.correction.op is CutOp.ROLES
        assert claimed.correction.roles == (Role.CLAIM,)

    assert Stage.ROLES not in found[0].questions
    (asked,) = found[1].questions[Stage.ROLES]
    assert closing in asked.handles.values()
    assert "no excerpt stands once" in asked.prompt
