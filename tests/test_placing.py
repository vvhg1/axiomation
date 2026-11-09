"""The placing (`docs/model.md` § 9): each block a line enters at a part,
whose own lines name no part they leave from, asked of an agent; the
lines it places written by code and given as one edges edit, a block it
reads whole filed under its key.

The chain of `test_update`, kept as an update's state: the setting's
block holds two sentences, its first the part [K] the definition's line
enters it at. An agent's edit gives the setting a line from its second
sentence, so the walk entering at [K] takes in a line [K] never writes.
No model is called.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from axiomation import edits, placing, versions
from axiomation.changes import Layer
from axiomation.cli import main
from axiomation.queries import View
from axiomation.questions import Stage
from axiomation.queue import Run, Served, Spool, opened
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.runner.queued import Price
from runner.fakes import FakeQueueHost, working_through
from test_marks import given, kept_update
from test_update import FLAT, SETTING

#: The setting's second sentence, as its block holds it after the update.
SECOND = "A second sentence of the setting, edited."
#: A line from the setting's second sentence: [K] does not write it.
SECOND_LINE = f'→ uses small-means-below-one · "{SECOND}"'
PLACED_LINE = f'→ uses [second] small-means-below-one · "{SECOND}"'
PART = f'  {SETTING} [second]: "{SECOND}"'
AGENT = edits.agent("model-a", "s1")


def to_place(tmp_path, author=AGENT):
    """The chain's state with the setting's line from its second sentence,
    given by `author`: the papers and the state."""
    v, _ = kept_update(tmp_path)

    def edit(sheet: str) -> str:
        return sheet.replace(
            '→ qualifies def:gain · "We fix a field of scalars."\n',
            f'→ qualifies def:gain · "We fix a field of scalars."\n{SECOND_LINE}\n',
        )

    made = given(v.store, edit, author)
    assert made.problems == () and made.state is not None, made.problems
    return v, made.state


def asked_at(store, state, named=()) -> list[placing.AskedBlock]:
    view = View(store, state)
    with edits.opened(store, state) as kept:
        return placing.asked_blocks(view, kept, named)


def sheet_at(store, state) -> str:
    with edits.opened(store, state) as kept:
        return edits.shown(kept, Layer.EDGES)


def task_of(served: str) -> str:
    return served.split()[1]


def place(served: str) -> tuple[str, dict]:
    """A block's answer: its second sentence a part, its line placed there."""
    return "placed", {
        "task": task_of(served),
        "why": "only the second sentence relies on the footnote's definition",
        "parts": [{"label": "second", "words": SECOND, "role": "assumption"}],
        "lines": [{"line": "L1", "from": ["second"]}],
    }


def whole(served: str) -> tuple[str, dict]:
    return "whole", {"task": task_of(served), "why": "the sentences are one thought"}


def placed(
    store, state, answer: Callable[[str], tuple[str, dict]] = place, named=(), **host
) -> tuple[placing.Done, FakeQueueHost]:
    """The placing's queue on `state`, a fake host's session answering each
    task with `answer`; then its edit given, as `place --ask` gives it."""
    fake = FakeQueueHost(working_through(answer), **host)
    run = Run(store, state, placing.FACTORY, {"blocks": list(named), "model": "m"})
    runner = Runner(fake, Resolver(store), 1.0, FLAT)
    return placing.ask(run, "m", runner, [fake], wait_seconds=0.2), fake


def served(store, state, tmp_path, **options) -> Served:
    workflow = opened(Run(store, state, placing.FACTORY, options)).__enter__()
    spool = Spool(tmp_path / "spool.jsonl")
    return Served(workflow, {}, spool, 0, "s0", wait=0.05, poll=0.01)


# --- which blocks are asked --------------------------------------------------------


def test_a_block_whose_entered_part_holds_all_its_words_is_not_asked(
    tmp_path,
) -> None:
    """The chain's definition is entered at [gain], which holds its every
    word, and the setting gives no uses line: nothing to place."""
    v, state = kept_update(tmp_path)
    assert placing.listed(View(v.store, state)) == []


def test_a_block_entered_at_a_part_giving_a_line_beyond_it_is_asked(
    tmp_path,
) -> None:
    v, state = to_place(tmp_path)
    (a,) = placing.listed(View(v.store, state))
    assert a.handle == SETTING
    assert [x.text for x in a.lines] == [SECOND_LINE]
    assert [x.owner for x in a.entering] == ["def:gain"]
    assert [p.label for p in a.parts] == ["K"]
    assert [x.owner for x in a.qualifiers] == []
    assert asked_at(v.store, state) == [a]
    # the queue's key and the listing's are one
    assert a.key == placing.to_place(sheet_at(v.store, state))[0].key


def test_a_placed_block_is_asked_no_more(tmp_path) -> None:
    v, state = to_place(tmp_path)
    sheet = sheet_at(v.store, state).replace(
        f"{SECOND_LINE}\n", f'→ uses [K] small-means-below-one · "{SECOND}"\n'
    )
    assert placing.to_place(sheet) == []


def test_a_line_placed_at_the_part_entered_is_the_agent_s_word(
    tmp_path,
) -> None:
    """[K] holds the first sentence alone, so the block is asked though it
    has one part. Placing the line at [K] narrows nothing, yet says where
    the line leaves from: the agent's word, accepted, never answered
    `whole`, which would say no line leaves from one part apart."""
    v, state = to_place(tmp_path)
    (a,) = asked_at(v.store, state)
    s = served(v.store, state, tmp_path)
    s.next()
    text, refused = s.call(
        "placed",
        {
            "task": f"b{a.key}",
            "why": "w",
            "lines": [{"line": "L1", "from": ["K"]}],
        },
    )
    assert not refused, text


# --- the task and its answers ------------------------------------------------------


def test_a_block_is_served_its_parts_and_lines_numbered(tmp_path) -> None:
    v, state = to_place(tmp_path)
    (a,) = asked_at(v.store, state)
    s = served(v.store, state, tmp_path)
    task = s.next().splitlines()
    assert task[:2] == [
        f"task b{a.key} (1 of 1)",
        "where does each line of this block leave from?",
    ]
    assert '  [K] "We fix a field of scalars."' in task
    assert f"L1 ⟦{SETTING}⟧ {SECOND_LINE}" in task
    assert any(x.startswith("  ⟦def:gain⟧ → uses we-fix-a-field [K]") for x in task)
    assert [t.name for t in s.tools][:3] == ["next", "placed", "whole"]
    brief = s.workflow.steps[0].template.text
    assert "## This session: where a block's lines leave from" in brief


def test_a_placed_answer_is_the_placing_s_edit(tmp_path) -> None:
    v, state = to_place(tmp_path)
    done, _ = placed(v.store, state)
    out = done.outcome
    assert [(s.block.handle, s.whole) for s in done.said] == [(SETTING, False)]
    assert out.state is not None and out.problems == (), out.problems
    edit = v.store.authoritative()[out.edit]
    assert edit.host != "person"
    assert edit.reason == (
        f"{SETTING}: only the second sentence relies on the footnote's definition"
    )
    sheet = sheet_at(v.store, out.state)
    assert PART in sheet and PLACED_LINE in sheet
    assert placing.listed(View(v.store, out.state)) == []
    # replayed, the same state
    run = Run(v.store, state, placing.FACTORY, {"blocks": [], "model": "m"})
    again = placing.ask(run, "m")
    assert (again.outcome.edit, again.outcome.state) == (out.edit, out.state)


def test_a_part_writing_another_is_its_sibling_line(tmp_path) -> None:
    v, state = to_place(tmp_path)

    def writes(served: str) -> tuple[str, dict]:
        tool, args = place(served)
        return tool, {
            **args,
            "writes": [{"part": "second", "uses": ["K"], "quote": "the setting"}],
        }

    done, _ = placed(v.store, state, writes)
    assert done.outcome.state is not None, done.outcome.problems
    sheet = sheet_at(v.store, done.outcome.state)
    assert f'→ uses [second] {SETTING} [K] · "the setting"' in sheet


def test_a_whole_answer_is_filed_and_not_asked_again(tmp_path) -> None:
    v, state = to_place(tmp_path)
    (a,) = asked_at(v.store, state)
    done, _ = placed(v.store, state, whole)
    assert done.outcome.state is None
    assert [(s.block.handle, s.whole) for s in done.said] == [(SETTING, True)]
    (filed,) = v.store.judgment_records().wholes
    assert (filed.state, filed.block, filed.reason) == (
        state.id,
        a.key,
        "the sentences are one thought",
    )
    assert asked_at(v.store, state) == []
    with pytest.raises(placing.PlaceError, match="is read whole by"):
        asked_at(v.store, state, [SETTING])
    # the run that read it whole still draws it, settled
    run = Run(v.store, state, placing.FACTORY, {"blocks": [], "model": "m"})
    assert placing.ask(run, "m").complete
    # a line changed makes it another block, asked again
    moved = given(
        v.store,
        lambda sheet: sheet.replace(SECOND_LINE, f"{SECOND_LINE} — the footnote"),
        AGENT,
    )
    assert moved.state is not None, moved.problems
    (again,) = asked_at(v.store, moved.state)
    assert again.key != a.key


def test_a_block_named_must_be_one_to_place(tmp_path) -> None:
    v, state = to_place(tmp_path)
    assert [a.handle for a in asked_at(v.store, state, [SETTING])] == [SETTING]
    with pytest.raises(placing.PlaceError, match="is no block to place"):
        asked_at(v.store, state, ["def:gain"])


@pytest.mark.parametrize(
    ("args", "refused"),
    [
        (
            {"lines": [{"line": "L1", "from": ["nowhere"]}]},
            "[nowhere], no part of the block",
        ),
        ({"lines": []}, "L1: place each line"),
        ({"lines": [{"line": "L1", "from": []}]}, "places no line"),
        (
            {
                "parts": [{"label": "second", "words": SECOND, "role": "claim"}],
                "lines": [{"line": "L1", "from": ["second"]}],
            },
            "never a claim",
        ),
        (
            {
                "parts": [
                    {"label": "second", "words": SECOND, "role": "assumption"},
                    {"label": "other", "words": "We fix", "role": "assumption"},
                ],
                "lines": [{"line": "L1", "from": ["second"]}],
            },
            "[other] is added and named by no line",
        ),
        (
            {
                "lines": [{"line": "L1", "from": ["K"]}],
                "writes": [{"part": "K", "uses": ["K"], "quote": "We fix"}],
            },
            "[K] at both ends",
        ),
        (
            {"lines": [{"line": "L2", "from": ["K"]}]},
            "the block's are L1 to L1",
        ),
        (
            {
                "parts": [
                    {"label": "s1", "words": "We fix a field", "role": "assumption"}
                ],
                "lines": [{"line": "L1", "from": ["s1"]}],
            },
            "its words share [K]'s",
        ),
    ],
)
def test_the_gate_refuses_what_no_placing_does(tmp_path, args, refused) -> None:
    v, state = to_place(tmp_path)
    (a,) = asked_at(v.store, state)
    s = served(v.store, state, tmp_path)
    s.next()
    text, was_refused = s.call("placed", {"task": f"b{a.key}", "why": "w", **args})
    assert was_refused and refused in text, text


def test_a_line_a_person_pinned_stays_as_written(tmp_path) -> None:
    """Given by a person, the line is pinned: shown so, and a placing of it
    refused; the block may be read whole."""
    v, state = to_place(tmp_path, edits.A_PERSON)
    (a,) = asked_at(v.store, state)
    assert [x.pinned for x in a.lines] == [True]
    s = served(v.store, state, tmp_path)
    assert "(a person's line, pinned: it stays)" in s.next()
    text, refused = s.call("placed", place(f"task b{a.key}")[1])
    assert refused and "a person's line, pinned" in text
    text, refused = s.call("whole", whole(f"task b{a.key}")[1])
    assert not refused, text


# --- the command and where it runs -------------------------------------------------


def test_place_lists_and_its_dry_run_writes_the_brief_and_tasks(
    tmp_path, capsys
) -> None:
    v, state = to_place(tmp_path)
    args = [str(v.new.root), "--store", str(v.store.directory)]
    out = tmp_path / "dry"
    capsys.readouterr()
    assert main(["place", *args]) == 0
    printed = capsys.readouterr().out
    assert f"1 blocks to place at state {state.id}, 1 not read whole" in printed
    assert f"  {SETTING}: entered at [K], 1 lines, 0 qualifiers  not read" in printed
    assert main(["place", *args, "--dry-run"]) == 1
    assert "--dry-run goes with --ask" in capsys.readouterr().err
    assert main(["place", *args, "--ask", "--dry-run", "--prompts", str(out)]) == 0
    printed = capsys.readouterr().out
    assert f"place --ask at state {state.id}: 1 blocks, 1 lines" in printed
    fitted = placing.FITTED
    assert (
        f"${fitted.task:.4f} a block, fitted on the circles question's recorded runs"
    ) in printed
    assert {p.name for p in out.iterdir()} == {f"{placing.KIND}.md", "tasks.md"}
    assert v.store.judgment_records().wholes == ()


def test_a_block_is_priced_by_what_the_store_s_sessions_cost(tmp_path) -> None:
    v, state = to_place(tmp_path)
    fitted = placing.FITTED
    assert placing.price(v.store, fitted.model) == Price(
        fitted.start, fitted.task, like="circles question"
    )
    placed(v.store, state, cost=0.05)
    assert placing.price(v.store, "fake/queue") == Price(0.0, 0.05, 1, 1)


def test_a_run_s_placing_is_asked_on_the_state_it_kept(tmp_path, monkeypatch) -> None:
    """The build's and the update's step (`place_after`) on a kept state
    with a block to place; with no runner, what the store does not hold is
    left unanswered; on a state with none, nothing is asked."""
    from axiomation.commands import asks, seating
    from axiomation.profiles import read_profiles

    v, state = to_place(tmp_path)
    profile = read_profiles()["standard"]
    agent = profile.agent(Stage.EDGES)
    queued = FakeQueueHost(working_through(place))
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: [queued])
    runner = Runner(queued, Resolver(v.store), 10.0, FLAT)
    unanswered, _ = asks.place_after(v.store, state, profile, None, None, agent)
    assert unanswered["unanswered"] == 1 and queued.sessions == []
    counts, stop = asks.place_after(v.store, state, profile, None, runner, agent)
    assert stop is None and (counts["placed"], counts["whole"]) == (1, 0)
    after = versions.state(v.store, counts["state"])
    assert asks.place_after(v.store, after, profile, None, runner, agent) is None


def test_an_update_dry_run_prices_the_placing_if_it_fires(tmp_path, capsys) -> None:
    """Priced on the blocks to place at the state it starts from, one at
    least; `--no-place` leaves it out."""
    from test_update import EDITED_SETTING, make_paper

    v, _ = to_place(tmp_path)
    third = make_paper(
        tmp_path / "third",
        main=EDITED_SETTING.replace(SECOND, "A second sentence, edited again."),
    )
    args = ["update", "--json", str(third.root), "--store", str(v.store.directory)]
    args += ["--profile", "standard", "--dry-run", "--json"]

    def priced(*flags: str) -> dict:
        capsys.readouterr()
        assert main([*args, *flags]) == 0
        out = capsys.readouterr().out
        return json.loads(out[out.index("{") :])

    fitted = placing.FITTED
    assert priced()["place_if_it_fires"] == round(fitted.start + fitted.task, 4)
    assert "place_if_it_fires" not in priced("--no-place")


def test_a_block_s_words_are_its_text_less_its_commands_markup() -> None:
    """An environment, a label, a reference or a citation is no word; a
    symbol's or a typeface's command, and its argument, are."""
    assert placing._covers(
        "\\begin{definition}\\label{d} ⟦…⟧ Let g be the gain.\\end{definition}",
        ["Let g be the gain"],
    )
    assert placing._covers("By~\\ref{x} we fix g.", ["By", "we fix g"])
    assert not placing._covers("\\textbf{Without g} we fix g.", ["we fix g"])
    assert not placing._covers("We fix g. $$\\Phi$$", ["We fix g."])


def test_a_qualifier_of_another_part_alone_asks_the_block(tmp_path) -> None:
    """The setting gives no uses line, but a qualifier names its second
    sentence's part: entered at [K], the walk takes it in, so it is asked."""
    v, state = kept_update(tmp_path)
    sheet = sheet_at(v.store, state)
    sheet = sheet.replace(
        '  we-fix-a-field [K]: "We fix a field of scalars."\n',
        f'  we-fix-a-field [K]: "We fix a field of scalars."\n{PART}\n',
    ).replace(
        "\\footnote{Small means below one.}\n",
        "\\footnote{Small means below one.}\n\n"
        '→ qualifies we-fix-a-field [second] · "Small means below one."\n',
    )
    (a,) = placing.to_place(sheet)
    assert (a.handle, a.lines, len(a.qualifiers)) == (SETTING, (), 1)


def test_an_unanswered_placing_leaves_the_circles_unasked(
    tmp_path, monkeypatch, capsys
) -> None:
    """An update's placing left open: the circles, which read what it
    places, wait for it, and the note says what to ask next."""
    from axiomation.commands import asks
    from test_circles import update_with_a_circle

    monkeypatch.setattr(
        asks, "place_after", lambda *_: ({"blocks": 1, "unanswered": 1}, None)
    )
    _, code, summary, _, _ = update_with_a_circle(tmp_path, monkeypatch, capsys)
    assert code == 0, summary
    assert summary["place"]["unanswered"] == 1 and "circles" not in summary


@pytest.mark.parametrize("command", ["build", "update", "sync"])
def test_no_place_is_each_round_s_flag(command) -> None:
    from axiomation.cli import parser

    found = parser().parse_args([command, "paper", "--no-place"])
    assert found.no_place is True
