"""A reread asked of an agent (`docs/architecture.md` § 9): the stale-marked
owners' lines read again after the update, as a queue (docs/architecture.md
§ 2): a task an owner, served with its lines numbered, its marks and what
changed at each marked line's far end; answered `stands`, filed with its
answer as a reread, or `changed`, its lines given at the run's end as the
reread's edges edit.

On the chain of `test_update` updated in one session and kept as a state
(`test_marks.kept_update`): the theorem and the sharpness marked stale by
the setting's edit. The sessions are a fake host's, scripted through the
tool server; no model is called.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from axiomation import rereads, versions
from axiomation.cli import main
from axiomation.marks import open_marks
from axiomation.queries import View
from axiomation.queue import Run, Served, Spool, opened
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.runner.hosts import HostOverBudget
from axiomation.runner.queued import Price
from runner.fakes import FakeQueueHost, working_through
from test_marks import NARROWED, REWORDED, SHARP_LINE, kept_update, record_of, rerun
from test_profiles import _profile
from test_update import (
    CHAIN,
    CHAIN_LEGEND,
    EDGES_FILE,
    EDITED_SETTING,
    FLAT,
    SETTING,
    SHARP,
    QueueThen,
    answering,
    make_paper,
)
from test_update import versions as two_papers
from test_update_single import THEOREM, as_assumption

STANDS = "the bound still holds of every field the setting now allows"
CHANGED = "the proof no longer covers it"


def asked(store, state, named=()) -> list[rereads.Owner]:
    return rereads.owners(View(store, state), named)


def stand(served: str) -> tuple[str, dict]:
    return "stands", {"task": served.split()[1], "why": STANDS}


def change_sharp(served: str) -> tuple[str, dict]:
    """The sharpness's line taken off, every other owner standing."""
    if f"⟦{SHARP}⟧" not in served.splitlines()[2]:
        return stand(served)
    (number,) = [
        line.split()[0] for line in served.splitlines() if line.endswith(SHARP_LINE)
    ]
    return "changed", {"task": served.split()[1], "why": CHANGED, "take_off": [number]}


def reread(
    store,
    state,
    answer: Callable[[str], tuple[str, dict]] = stand,
    named=(),
    **host,
) -> tuple[rereads.Done, FakeQueueHost]:
    """The reread's queue on `state`, a fake host's session answering each
    task with `answer`; then its edit given, as `reread --ask` gives it."""
    fake = FakeQueueHost(working_through(answer), **host)
    run = Run(store, state, rereads.FACTORY, {"owners": list(named), "model": "m"})
    runner = Runner(fake, Resolver(store), 1.0, FLAT)
    return rereads.ask(run, "m", runner, [fake], wait_seconds=0.2), fake


def served(store, state, tmp_path, **options) -> Served:
    workflow = opened(Run(store, state, rereads.FACTORY, options)).__enter__()
    spool = Spool(tmp_path / "spool.jsonl")
    return Served(workflow, {}, spool, 0, "s0", wait=0.05, poll=0.01)


# --- a task ------------------------------------------------------------------------


def test_an_owner_is_served_its_lines_marks_and_what_changed(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    assert [o.handle for o in asked(v.store, state)] == [THEOREM, SHARP]
    s = served(v.store, state, tmp_path)
    task = s.next()
    head, ask, place, *rest = task.splitlines()
    assert (head, ask) == ("task o1 (1 of 2)", "do this owner's lines still read true?")
    assert place.startswith(f"block ⟦{THEOREM}⟧ ")
    assert "its lines:" in rest and rest[rest.index("its lines:") + 1].startswith("L1 ")
    assert any(line.startswith("⟦stale since ") for line in rest)
    assert "before:" in rest and "now:" in rest
    assert any(line.startswith("at its far end, ⟦") for line in rest)
    assert any(SETTING in line for line in rest)
    assert rest[-1].startswith("pointers: ⟦")
    assert [t.name for t in s.tools][:3] == ["next", "stands", "changed"]
    # the writer's notation, then the reread's own section, as one brief
    brief = s.workflow.steps[0].template.text
    assert "## This session: lines marked stale, one owner at a time" in brief
    assert brief.index("## This session") > 0


# --- the run -----------------------------------------------------------------------


def test_owners_whose_lines_stand_are_filed_as_the_agent_s_rereads(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    done, fake = reread(v.store, state)

    assert done.complete and done.outcome.state is None
    assert [(s.owner.handle, s.stands, s.why) for s in done.said] == [
        (THEOREM, True, STANDS),
        (SHARP, True, STANDS),
    ]
    # a filing: no state, every mark closed, by an agent
    assert versions.latest(v.store) == state
    assert open_marks(v.store, state) == ()
    cleared = View(v.store, state).cleared_marks
    assert {c.mark.handle for c in cleared} == {THEOREM, SHARP}
    assert not any(c.by_person for c in cleared)
    assert {c.reread.reason for c in cleared if c.reread} == {STANDS}
    assert fake.sessions[0].context_tokens is None


@pytest.mark.usefixtures("develop_mode")
def test_a_changed_owner_s_lines_are_the_reread_s_edges_edit(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    done, _ = reread(v.store, state, change_sharp)

    out = done.outcome
    assert [(s.owner.handle, s.stands) for s in done.said] == [
        (THEOREM, True),
        (SHARP, False),
    ]
    assert out.state is not None and out.edit is not None, out.problems
    edit = v.store.authoritative()[out.edit]
    assert edit.reason == f"{SHARP}: {CHANGED}"
    record = record_of(v.store, out.state)
    (sharp,) = [s for s in record["stale"] if s["handle"] == SHARP]
    assert sharp["cleared_by"] == out.edit
    assert open_marks(v.store, out.state) == ()
    # the edit replays as any agent's edit does
    assert rerun(v) == versions.kept_export(v.store, out.state)


def test_a_complete_reread_replays_from_the_store_to_its_state(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    done, _ = reread(v.store, state, change_sharp)
    run = Run(v.store, state, rereads.FACTORY, {"owners": [], "model": "m"})
    again = rereads.ask(run, "m")
    assert again.outcome.report is None and again.complete
    assert (again.outcome.edit, again.outcome.state) == (
        done.outcome.edit,
        done.outcome.state,
    )
    assert [(s.owner.handle, s.stands) for s in again.said] == [
        (THEOREM, True),
        (SHARP, False),
    ]


def test_a_stopped_reread_resumes_on_the_owners_it_drew(tmp_path) -> None:
    """Stopped after the theorem stands, its reread filed clears its mark;
    the run resumed still draws the theorem, settled, and serves the
    sharpness alone."""
    v, state = kept_update(tmp_path)
    first = FakeQueueHost(working_through(stand, stop_after=1), ends=HostOverBudget)
    run = Run(v.store, state, rereads.FACTORY, {"owners": [], "model": "m"})
    stopped = rereads.ask(
        run, "m", Runner(first, Resolver(v.store), 1.0, FLAT), [first], wait_seconds=0.2
    )
    assert not stopped.complete and stopped.outcome.open == 1
    assert [o.handle for o in asked(v.store, state)] == [SHARP]

    served_again: list[str] = []

    def answer(task: str) -> tuple[str, dict]:
        served_again.append(task.split()[1])
        return change_sharp(task)

    second = FakeQueueHost(working_through(answer))
    done = rereads.ask(
        run,
        "m",
        Runner(second, Resolver(v.store), 1.0, FLAT),
        [second],
        wait_seconds=0.2,
    )
    assert done.complete and done.outcome.state is not None
    # the theorem's task keys as it did: it is not served again
    assert served_again == ["o2"]
    assert [(s.owner.handle, s.stands) for s in done.said] == [
        (THEOREM, True),
        (SHARP, False),
    ]


def test_a_describing_about_line_put_on_stands_no_claim(tmp_path) -> None:
    """The reread's lines are the edges writer's: an about line naming no
    claim it restates describes its target, so the sharpness, its block's
    one claim, stands unsupported still in the state the edit makes."""
    from test_support_search import standing

    v, state = kept_update(tmp_path)
    about = f'→ about {THEOREM} · "Next, the bound is sharp."'

    def describe(served: str) -> tuple[str, dict]:
        tool, args = change_sharp(served)
        return tool, ({**args, "put_on": [about]} if tool == "changed" else args)

    done, _ = reread(v.store, state, describe)
    made = done.outcome.state
    assert made is not None, done.outcome.problems
    assert standing(v.store, made, "Next, the bound is sharp.") == "unsupported"


@pytest.mark.parametrize(
    ("tool", "args", "problem"),
    [
        ("stands", {"why": " "}, "give the reason (why)"),
        ("changed", {"why": "w"}, "takes a line off or puts one on"),
        ("changed", {"why": "w", "take_off": ["L9"]}, "take_off 'L9'"),
        ("changed", {"why": "w", "put_on": ["uses X"]}, "put_on 'uses X'"),
        (
            "changed",
            {"why": "w", "put_on": [f'→ uses nowhere · "{SETTING}"']},
            "nowhere",
        ),
    ],
)
def test_an_answer_the_gate_refuses_is_refused_with_why(
    tmp_path, tool, args, problem
) -> None:
    v, state = kept_update(tmp_path)
    s = served(v.store, state, tmp_path)
    s.next()
    text, refused = s.call(tool, {"task": "o1", **args})
    assert refused and problem in text, text
    assert s.spool.entries() == []


def test_a_named_owner_with_no_open_mark_is_refused(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    assert [o.handle for o in asked(v.store, state, [SHARP])] == [SHARP]
    with pytest.raises(rereads.RereadError, match="holds no open stale mark"):
        asked(v.store, state, [SETTING])


def test_a_state_that_takes_no_edit_is_refused_before_it_is_asked(
    tmp_path, monkeypatch, capsys
) -> None:
    """A reread of the update's state once a later edit state of its version
    is kept: the lines it changed could not be given, so nothing is asked
    or paid for, and the command says why."""
    from axiomation.commands import seating

    v, state = kept_update(tmp_path)
    done, _ = reread(v.store, state, change_sharp, named=[SHARP])
    assert done.outcome.state is not None
    # the sharpness's mark closes at the edit's state, not at the update's
    assert [o.handle for o in asked(v.store, state)] == [THEOREM, SHARP]

    fake = FakeQueueHost(working_through(stand))
    run = Run(v.store, state, rereads.FACTORY, {"owners": [], "model": "m"})
    with pytest.raises(rereads.RereadError, match="not its version's latest"):
        rereads.ask(run, "m", Runner(fake, Resolver(v.store), 1.0, FLAT), [fake])
    assert fake.sessions == []

    monkeypatch.setattr(seating, "queue_hosts", lambda seated: [fake])
    args = [str(v.new.root), "--store", str(v.store.directory), "--state", state.id]
    capsys.readouterr()
    assert main(["reread", *args, "--ask", "--budget", "1", "--rates", "3,15"]) == 1
    assert "not asked: state" in capsys.readouterr().err
    assert fake.sessions == []


def test_reread_ask_dry_run_writes_the_brief_and_tasks_and_prices_them(
    tmp_path, capsys
) -> None:
    v, state = kept_update(tmp_path)
    args = [str(v.new.root), "--store", str(v.store.directory)]
    out = tmp_path / "dry"
    capsys.readouterr()

    # priced with no directory, nothing written
    assert main(["reread", *args, "--ask", "--dry-run"]) == 0
    alone = capsys.readouterr().out
    assert f"reread --ask at state {state.id}: 2 owners" in alone
    assert "under" not in alone and not out.exists()
    for flags in (["--dry-run"], ["--prompts", str(out)]):
        assert main(["reread", *args, SHARP, "--reason", "r", *flags]) == 1
        assert "goes with --ask" in capsys.readouterr().err

    assert main(["reread", *args, "--ask", "--dry-run", "--prompts", str(out)]) == 0
    printed = capsys.readouterr().out
    assert f"  brief and tasks under {out}" in printed
    assert f"reread --ask at state {state.id}: 2 owners" in printed
    fitted = rereads.FITTED
    assert f"expected ${fitted.start + 2 * fitted.task:.2f}: 2 tasks" in printed
    assert f"${fitted.start:.4f} its session's start" in printed
    assert f"${fitted.task:.4f} an owner, fitted on recorded runs" in printed
    assert {p.name for p in out.iterdir()} == {f"{rereads.KIND}.md", "tasks.md"}
    tasks = (out / "tasks.md").read_text(encoding="utf-8")
    assert tasks.startswith("task o1\n") and "task o2\n" in tasks
    # nothing asked, nothing filed
    assert {m.handle for m in open_marks(v.store, state)} == {THEOREM, SHARP}

    assert main(["reread", *args, SHARP]) == 1
    assert "gives --reason" in capsys.readouterr().err


def test_reread_ask_with_no_host_replays_what_the_store_holds(
    tmp_path, monkeypatch, capsys
) -> None:
    from axiomation.commands import seating

    v, state = kept_update(tmp_path)
    args = [str(v.new.root), "--store", str(v.store.directory), "--state", state.id]
    capsys.readouterr()
    assert main(["reread", *args, "--ask", "--host", "none"]) == 0
    assert "2 owners open: nothing given yet; not asked" in capsys.readouterr().out

    fake = FakeQueueHost(working_through(change_sharp))
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: [fake])
    asking = ["--budget", "1", "--rates", "3,15"]
    assert main(["reread", *args, "--ask", *asking]) == 0
    first = capsys.readouterr().out
    assert f"stands: {THEOREM} — {STANDS}" in first
    assert f"changed: {SHARP} — {CHANGED}" in first
    made = versions.latest(v.store)
    assert f"state {made.id}" in first

    # replayed under the profile it was asked under, which keys its run
    assert (
        main(["reread", *args, "--ask", "--host", "none", "--profile", "standard"]) == 0
    )
    again = capsys.readouterr().out
    # the same owners settled and the same state, nothing spent; the gates
    # are read against the store as it stands now
    assert again.split("\n")[1:4] == first.split("\n")[2:5]
    assert len(fake.sessions) == 1


def test_a_description_is_asked_after_the_gate_s_marks(tmp_path) -> None:
    """Of `test_mark_lines`' two about lines onto t, r's, under its
    `restated` standing, is the gate's and asked first; h's describes t
    and is asked after it, or alone where named."""
    from test_mark_lines import built, cause, updated

    store, first = built(tmp_path)
    marked = {"h": [cause("h", "about", "t")], "r": [cause("r", "about", "t")]}
    edited = updated(store, first, marked, changed=("t",), deltas=("t",))
    view = View(store, edited)
    assert [o.handle for o in rereads.owners(view)] == ["r", "h"]
    (h,) = rereads.owners(view, ["h"])
    assert [m.gate for m in h.marks] == [False]


def test_an_owner_s_reread_is_priced_by_what_the_store_s_sessions_cost(
    tmp_path,
) -> None:
    """The fitted default where the store holds no session; then the
    start fitted, and the tasks what the sessions cost less their starts.
    A model with no rates is priced by its sessions alone."""
    v, state = kept_update(tmp_path)
    fitted = rereads.FITTED
    price = rereads.price(v.store, fitted.model)
    assert price == Price(fitted.start, fitted.task)
    assert price.cost(0) == 0.0
    assert price.cost(2) == pytest.approx(fitted.start + 2 * fitted.task)
    # a start for each session the tasks take
    assert price.cost(3, 2) == pytest.approx(2 * fitted.start + 3 * fitted.task)
    with pytest.raises(rereads.RereadError, match="no rates are known for nothing"):
        rereads.price(v.store, "nothing")
    reread(v.store, state, cost=0.04)
    assert rereads.price(v.store, "fake/queue") == Price(0.0, 0.02, 1, 2)


def test_a_session_s_start_is_taken_off_before_its_tasks_are_priced(
    tmp_path,
) -> None:
    v, state = kept_update(tmp_path)
    fitted = rereads.FITTED
    reread(v.store, state, cost=0.40, model=fitted.model)
    price = rereads.price(v.store, fitted.model)
    assert (price.start, price.sessions, price.tasks) == (fitted.start, 1, 2)
    assert price.task == pytest.approx((0.40 - fitted.start) / 2)


# --- the update ends with the reread ------------------------------------------------


#: An update in one session, its moved blocks carried wholly (a cap of 0 Δ
#: lines), so the rule marks the theorem and the sharpness; its end steps
#: but the search's, which `test_support_search` holds
SINGLE = (
    "per_stage_update_above_recut_blocks = 1000\nmoved_delta_lines_max = 0\n"
    "search_support_after_edges = false\n"
)


def update_cli(
    tmp_path,
    monkeypatch,
    capsys,
    *flags: str,
    delta: str = NARROWED,
    settings: str = "",
    cost: float = 0.01,
    budget: str = "5",
    answer: Callable[[str], tuple[str, dict]] = stand,
):
    """`update` from the command line on the chain, the setting's Δ
    `delta`, under `SINGLE` and `settings`; a fake queue host answering
    the update's tasks as the edges' file host would edit the sheet
    (`queue_then`), and each reread task with `answer` (every owner
    standing). The papers, the exit code, the
    summary, stderr and both hosts."""
    from axiomation.commands import seating
    from runner.fakes import FakeHost

    v = two_papers(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    profiles = tmp_path / "profiles.toml"
    profiles.write_text(_profile("p", SINGLE + settings), encoding="utf-8")
    fake = FakeHost(
        lambda _: "",
        edits={EDGES_FILE: answering({SETTING: [delta]}, as_assumption)},
        cost=cost,
    )
    answers = QueueThen(v, fake.edits[EDGES_FILE], answer)
    queued = FakeQueueHost(working_through(answers), cost=cost)
    monkeypatch.setattr(seating, "host_of", lambda agent, executable, tools=None: fake)
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: [queued])
    monkeypatch.setattr(
        seating,
        "executables_of",
        lambda agents: {a.harness: "fake" for a in agents.values()},
    )
    where = [str(v.new.root), "--store", str(v.store.directory)]
    capsys.readouterr()
    code = main(
        [
            "update",
            "--json",
            *where,
            *("--profiles", str(profiles), "--profile", "p", "--rates", "3,15"),
            *("--budget", budget),
            *flags,
        ]
    )
    printed = capsys.readouterr()
    assert "{" in printed.out, (code, printed.err)
    assert not answers.update.unserved(), answers.update.unserved()
    summary = json.loads(printed.out[printed.out.index("{") :])
    return v, code, summary, printed.err, fake, queued


def test_an_update_ends_with_the_reread_of_the_marks_it_left(
    tmp_path, monkeypatch, capsys
) -> None:
    v, code, summary, err, fake, queued = update_cli(tmp_path, monkeypatch, capsys)

    assert code == 0, (summary, err)
    kept = versions.latest(v.store)
    assert summary["state"]["id"] == kept.id
    assert summary["reach"]["open_marks"] == 2
    found = summary["reread"]
    assert (found["owners"], found["marks"]) == (2, 2)
    assert (found["stands"], found["changed"], found["open_marks"]) == (2, 0, 0)
    assert "state" not in found  # no line changed: a filing, no state
    # the update's queue, then the reread's; the edges' file host never
    assert fake.calls == 0 and len(queued.sessions) == 2
    assert queued.sessions[1].context_tokens == 100_000
    assert open_marks(v.store, kept) == ()


def test_an_end_step_that_raises_still_prints_what_the_update_kept_and_spent(
    tmp_path, monkeypatch, capsys
) -> None:
    from axiomation.commands import asks

    def raises(*_: object) -> None:
        raise RuntimeError("the sheet does not render")

    monkeypatch.setattr(asks, "circles_after", raises)

    with pytest.raises(RuntimeError, match="does not render"):
        update_cli(tmp_path, monkeypatch, capsys)

    out = capsys.readouterr().out
    summary = json.loads(out[out.index("{") :])
    assert summary["state"]["id"]
    assert summary["reread"]["stands"] == 2
    assert summary["spent"] > 0


def test_a_line_the_update_s_reread_changes_is_its_edges_edit(
    tmp_path, monkeypatch, capsys
) -> None:
    v, code, summary, _, _, _ = update_cli(
        tmp_path, monkeypatch, capsys, answer=change_sharp
    )

    assert code == 0
    found = summary["reread"]
    assert (found["stands"], found["changed"], found["open_marks"]) == (1, 1, 0)
    edited = versions.latest(v.store)
    assert found["state"] == edited.id != summary["state"]["id"]
    assert edited.parent == summary["state"]["id"]
    assert open_marks(v.store, edited) == ()


@pytest.mark.parametrize(
    ("flags", "settings"),
    [(("--no-reread",), ""), ((), "reread_marks_after_update = false\n")],
)
def test_no_reread_or_the_setting_off_leaves_the_marks_open(
    tmp_path, monkeypatch, capsys, flags, settings
) -> None:
    v, code, summary, _, fake, queued = update_cli(
        tmp_path, monkeypatch, capsys, *flags, settings=settings
    )

    assert code == 0 and "reread" not in summary
    assert fake.calls == 0 and len(queued.sessions) == 1
    assert {m.handle for m in open_marks(v.store, versions.latest(v.store))} == {
        THEOREM,
        SHARP,
    }


def test_a_budget_the_update_spent_keeps_its_state_and_names_reread_ask(
    tmp_path, monkeypatch, capsys
) -> None:
    v, code, summary, err, fake, queued = update_cli(
        tmp_path, monkeypatch, capsys, cost=1.0, budget="1"
    )

    assert code == 0
    assert summary["reread"]["stopped"] == "budget"
    assert summary["reread"]["unanswered"] == 2
    assert "reread PAPER --ask" in err
    assert fake.calls == 0 and len(queued.sessions) == 1
    kept = versions.latest(v.store)
    assert summary["state"]["id"] == kept.id
    assert len(open_marks(v.store, kept)) == 2


def test_an_update_with_no_open_mark_asks_nothing_more(
    tmp_path, monkeypatch, capsys
) -> None:
    v, code, summary, _, fake, queued = update_cli(
        tmp_path, monkeypatch, capsys, delta=REWORDED
    )

    assert code == 0 and "reread" not in summary
    assert fake.calls == 0 and len(queued.sessions) == 1
    assert open_marks(v.store, versions.latest(v.store)) == ()


def test_an_update_dry_run_prices_the_reread_if_it_fires(tmp_path, capsys) -> None:
    """Priced on the owners open at the state it starts from, one at least:
    the update's own marks are not known before its session."""
    v, _ = kept_update(tmp_path)
    third = make_paper(
        tmp_path / "third",
        main=EDITED_SETTING.replace(
            "A second sentence of the setting, edited.",
            "A second sentence of the setting, edited again.",
        ),
    )
    args = ["update", "--json", str(third.root), "--store", str(v.store.directory)]
    args += ["--profile", "standard", "--dry-run", "--json"]

    def priced(*flags: str) -> dict:
        capsys.readouterr()
        assert main([*args, *flags]) == 0
        out = capsys.readouterr().out
        return json.loads(out[out.index("{") :])

    fitted = rereads.FITTED
    assert priced()["reread_if_it_fires"] == round(fitted.start + 2 * fitted.task, 4)
    assert "reread_if_it_fires" not in priced("--no-reread")
