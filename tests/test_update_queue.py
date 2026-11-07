"""An update's one session asked as a queue (docs/architecture.md § 2):
the blocks the edit touched served one at a time, each answer laid on the
carried sheet by code, the sheet filed as the session's answer.

On the chain of `test_update`: the setting edited and re-cut, updated from
the command line. The update's tasks and the reread at its end are a fake
queue host's, scripted through the tool server; no model is called, and
the edges' file host is never asked.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable

import pytest

import record_file
from axiomation import versions
from axiomation.cli import main
from axiomation.queue import AGAIN, EMPTY, ENDED, SHARE_DONE
from axiomation.reader import document_hash, read_paper
from axiomation.update_queue import KIND
from questions.review_paper import MAIN
from runner.fakes import FakeHost, FakeQueueHost
from test_marks import NARROWED
from test_profiles import _profile
from test_rereads import SINGLE, stand
from test_update import (
    CHAIN,
    CHAIN_LEGEND,
    DEFINITION,
    EDGES_FILE,
    EDITED_SETTING,
    PROOF_BLOCK,
    SETTING,
    answering,
)
from test_update import versions as two_papers
from test_update_single import as_assumption

SMALL = "the gain is small"
NOTE = NARROWED.split("— ", 1)[1]


def is_update_task(served: str) -> bool:
    # the reread's tasks are named o1, o2, …; the update's by their blocks
    return not re.fullmatch(r"o\d+", served.split()[1])


def narrowing(served: str) -> tuple[str, dict]:
    """The setting narrowed and given its role; every other block's lines
    standing; a reread task standing."""
    task = served.split()[1]
    if not is_update_task(served):
        return stand(served)
    if task == SETTING:
        return "answer", {
            "task": task,
            "delta": {"class": "narrowed", "note": NOTE},
            "roles": "assumption",
        }
    return "answer", {"task": task}


def scripted(
    answer: Callable[[str], tuple[str, dict]], refused: list[str]
) -> Callable[[Callable[[str, dict], tuple[str, bool]]], str]:
    """A session's script: each task answered by `answer`; a refusal
    recorded in `refused` and the task answered again by `narrowing`."""

    def script(call: Callable[[str, dict], tuple[str, bool]]) -> str:
        text, _ = call("next", {})
        while text not in (EMPTY, ENDED, SHARE_DONE):
            if text == AGAIN:
                text, _ = call("next", {})
                continue
            tool, args = answer(text)
            reply, was_refused = call(tool, args)
            if was_refused:
                refused.append(reply)
                reply, was_refused = call(*narrowing(text))
                assert not was_refused, reply
            text = reply.split("\n\n", 1)[1] if reply.startswith("accepted") else reply
        return "done"

    return script


def kept_old(v, tmp_path) -> versions.State:
    """OLD's export kept as its build's state: the state the update starts
    from, which the queue runs on."""
    document = read_paper(v.old.root)
    state, _ = versions.record(
        v.store,
        versions.snapshot(v.old.root, document),
        document_hash(document),
        document.root,
        (tmp_path / "old.json").read_bytes(),
        versions.Command.BUILD,
        None,
        None,
    )
    return state


def update_cli(
    tmp_path,
    monkeypatch,
    capsys,
    answer: Callable[[str], tuple[str, dict]] = narrowing,
    settings: str = "",
    edited: str = EDITED_SETTING,
):
    """`update` on the chain under `SINGLE` and `settings`, the update's
    queue and the reread answered by one fake queue host; the papers, the
    exit code, the summary, the one session's host, the queue's host, and
    the refusals its script met."""
    from axiomation.commands import seating

    v = two_papers(tmp_path, edited, CHAIN, CHAIN_LEGEND)
    kept_old(v, tmp_path)
    profiles = tmp_path / "profiles.toml"
    profiles.write_text(_profile("p", SINGLE + settings), encoding="utf-8")
    session = FakeHost(
        lambda _: "",
        edits={EDGES_FILE: answering({SETTING: [NARROWED]}, as_assumption)},
    )
    refused: list[str] = []
    queued = FakeQueueHost(scripted(answer, refused))
    monkeypatch.setattr(
        seating, "host_of", lambda agent, executable, tools=None: session
    )
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: [queued])
    monkeypatch.setattr(
        seating,
        "executables_of",
        lambda agents: {a.harness: "fake" for a in agents.values()},
    )
    capsys.readouterr()
    code = main(
        [
            "update",
            "--json",
            str(v.new.root),
            *("--store", str(v.store.directory)),
            *("--profiles", str(profiles), "--profile", "p", "--rates", "3,15"),
            *("--budget", "5"),
        ]
    )
    printed = capsys.readouterr()
    assert "{" in printed.out, (code, printed.err)
    summary = json.loads(printed.out[printed.out.index("{") :])
    return v, code, summary, session, queued, refused


@pytest.mark.usefixtures("develop_mode")
def test_the_queue_answers_the_update_s_question_and_its_marks_follow(
    tmp_path, monkeypatch, capsys
) -> None:
    v, code, summary, session, queued, refused = update_cli(
        tmp_path, monkeypatch, capsys
    )

    assert code == 0, summary
    assert session.calls == 0
    assert refused == []
    tasks = [a for a in v.store.answers() if a.kind == KIND]
    assert SETTING in {a.call["args"]["task"] for a in tasks}
    edges = [a for a in v.store.answers() if a.host == "queue"]
    assert len(edges) == 1
    sheet = record_file.text(v.store.directory, edges[0])
    assert f"⟦{SETTING} assumption⟧" in sheet
    assert NARROWED in sheet
    # the queue's answer costs nothing; its sessions, listed beside it, bear it
    export = json.loads(versions.kept_export(v.store, versions.latest(v.store)))
    costs = export["review"]["update"]["cost"]
    assert costs[edges[0].key]["cost"] == 0
    assert sum(c["cost"] for c in costs.values()) == len(queued.sessions) * 0.01


def test_a_changed_block_is_served_before_and_now_and_owes_its_delta(
    tmp_path, monkeypatch, capsys
) -> None:
    served: dict[str, str] = {}

    def without_delta(text: str) -> tuple[str, dict]:
        task = text.split()[1]
        served[task] = text
        if task == SETTING:
            return "answer", {"task": task, "roles": "assumption"}
        return narrowing(text)

    _, code, summary, _, _, refused = update_cli(
        tmp_path, monkeypatch, capsys, without_delta
    )

    assert code == 0, summary
    shown = served[SETTING]
    assert "before:" in shown and "now:" in shown
    assert "A second sentence of the setting, edited." in shown
    assert "its roles are open" in shown
    assert len(refused) == 1
    assert f"no Δ line ({SETTING})" in refused[0]


def test_a_role_for_a_block_whose_roles_are_fixed_is_refused(
    tmp_path, monkeypatch, capsys
) -> None:
    def roles_everywhere(text: str) -> tuple[str, dict]:
        tool, args = narrowing(text)
        if is_update_task(text) and args["task"] != SETTING:
            args = {**args, "roles": "claim"}
        return tool, args

    _, code, _, _, _, refused = update_cli(
        tmp_path, monkeypatch, capsys, roles_everywhere
    )

    assert code == 0
    assert refused and all("roles are fixed" in r for r in refused)


@pytest.mark.usefixtures("develop_mode")
def test_a_block_writing_a_new_part_s_words_is_asked_whether_it_relies_on_it(
    tmp_path, monkeypatch, capsys
) -> None:
    served: dict[str, str] = {}

    def with_part(text: str) -> tuple[str, dict]:
        task = text.split()[1]
        served[task] = text
        tool, args = narrowing(text)
        if task == SETTING:
            args = {**args, "parts": [{"label": "small", "words": SMALL}]}
        return tool, args

    edited = MAIN.replace(
        "A second sentence of the setting.",
        f"A second sentence of the setting, edited: {SMALL}.",
    )
    v, code, summary, _, _, refused = update_cli(
        tmp_path, monkeypatch, capsys, with_part, edited=edited
    )

    assert code == 0, summary
    assert refused == []
    shown = served[PROOF_BLOCK]
    assert "does it rely on what the edit wrote?" in shown
    assert f'it writes "{SMALL}", {SETTING} [small]' in shown
    assert "the block it may rely on:" in shown
    (edges,) = [a for a in v.store.answers() if a.host == "queue"]
    assert f'  {SETTING} [small]: "{SMALL}"' in record_file.text(
        v.store.directory, edges
    )


# --- on the update's own run: a split, an answered question, a replay ----------------


def queued(tmp_path, answer, settings=None, edited: str = EDITED_SETTING, lines=CHAIN):
    """The chain updated from its identity answer alone (no kept state), the
    one session asked as a queue of a fake host answering `answer`; the
    papers, the update, the queue's host and the refusals its script met."""
    from axiomation.resolver import Resolver
    from axiomation.runner import Runner
    from axiomation.update import run as run_update
    from axiomation.update_queue import Seats
    from test_update import FLAT
    from test_update_single import CARRIED_SETTINGS

    v = two_papers(tmp_path, edited, lines, CHAIN_LEGEND)
    refused: list[str] = []
    host = FakeQueueHost(scripted(answer, refused))
    base = settings or CARRIED_SETTINGS
    with_queue = dataclasses.replace(base, paper=v.new.root, edges_queue=Seats((host,)))
    runner = Runner(FakeHost(lambda _: ""), Resolver(v.store), 10.0, FLAT)
    made = run_update(v.new.document, v.store, with_queue, runner)
    return v, made, host, refused, with_queue


SPLIT = (
    '✂ split "A second sentence" · "A second sentence of the setting, edited." — '
    "a remark after the assumption"
)


def splitting(served: str) -> tuple[str, dict]:
    """The setting split at its second sentence, its first part an
    assumption; the piece the split made given an edge onto the setting;
    every other task answered with nothing."""
    task = served.split()[1]
    if task == SETTING:
        return "answer", {
            "task": task,
            "delta": {"class": "narrowed", "note": NOTE},
            "roles": 'assumption | motivation "A second sentence"',
            "put_on": [SPLIT],
        }
    if "a changed block" in served and not task.startswith(SETTING):
        return "answer", {
            "task": task,
            "put_on": [f'→ uses {SETTING} · "A second sentence"'],
        }
    return "answer", {"task": task}


@pytest.mark.usefixtures("develop_mode")
def test_a_split_is_applied_at_once_and_its_blocks_are_served_again(
    tmp_path,
) -> None:
    from axiomation.exchange import dumps
    from axiomation.update import run as run_update
    from axiomation.update import update_payload

    v, made, host, refused, settings = queued(tmp_path, splitting)

    assert made.complete, made.unanswered
    assert refused == []
    tasks = [a.call["args"]["task"] for a in v.store.answers() if a.kind == KIND]
    # the setting, opened again by its own split, is served a second time
    assert tasks.count(SETTING) == 1 and f"{SETTING}#2" in tasks
    (edges,) = [a for a in v.store.answers() if a.host == "queue"]
    assert "Applied: " in record_file.text(v.store.directory, edges)
    exported = update_payload(v.new.document, made, v.store)
    by_id = {b["id"]: b for b in exported["blocks"]}
    (new_id,) = [b for b in by_id if b.startswith("main.tex:5:")]
    assert by_id[new_id]["roles"] == ["motivation"]
    assert (new_id, "uses") in {(e["source"], e["kind"]) for e in exported["edges"]}
    # replayed from the store, the same export
    replayed = update_payload(
        v.new.document, run_update(v.new.document, v.store, settings, None), v.store
    )
    assert dumps(replayed) == dumps(exported)


@pytest.mark.usefixtures("develop_mode")
def test_a_split_s_reach_is_walked_from_the_piece_that_speaks_for_its_block(
    tmp_path,
) -> None:
    """The setting split: its first piece keeps its roles and its qualifies
    line, the split is an applied correction, and the Δ the setting was
    given is walked from that piece: the definition it moves is asked its
    Δ, which closes it."""
    from axiomation.update import update_payload
    from test_update_single import NARROWED_CLOSED, PLAIN_SETTINGS

    unchanged = NARROWED_CLOSED[DEFINITION][0].split("— ", 1)[1]

    def answer(text: str) -> tuple[str, dict]:
        task = text.split()[1]
        if text.split("\n")[1].startswith("a block whose meaning moved"):
            return "answer", {
                "task": task,
                "delta": {"class": "unchanged", "note": unchanged},
            }
        return splitting(text)

    v, made, _, refused, _ = queued(tmp_path, answer, settings=PLAIN_SETTINGS)

    assert made.complete, made.unanswered
    assert refused == []
    exported = update_payload(v.new.document, made, v.store)
    setting = made.plan.handles[SETTING]
    by_id = {b["id"]: b for b in exported["blocks"]}
    assert by_id[setting]["roles"] == ["assumption"]
    applied = [c["op"] for c in exported["review"]["cut_corrections"] if c["applied"]]
    assert "split" in applied
    assert (setting, "qualifies") in {
        (e["source"], e["kind"]) for e in exported["edges"]
    }
    found = exported["review"]["update"]
    assert [
        (d["handle"], d["class"]) for d in found["deltas"] if d["by"] == "writer"
    ] == [(SETTING, "narrowed"), (DEFINITION, "unchanged")]
    assert found["closed_by_delta"] == [DEFINITION]
    assert DEFINITION not in {m["handle"] for m in found["moved_by_rule"]}


#: A paragraph the edit adds after the setting's split: its task comes
#: after the setting's.
ADDED = "Every input of the results is finite."


@pytest.mark.usefixtures("develop_mode")
def test_a_block_the_edit_added_still_waits_for_its_task_after_a_split(
    tmp_path,
) -> None:
    """A split is applied at once and the sheet carried onto its cut; the
    blocks still waiting for their tasks stay open, an added one with them,
    so it is served and written."""
    served: dict[str, str] = {}

    def answer(text: str) -> tuple[str, dict]:
        task = text.split()[1]
        served[task] = text
        if text.split("\n")[1].startswith("an added block"):
            return "answer", {
                "task": task,
                "roles": "claim",
                "put_on": [f'⊢ unsupported · "{ADDED}"'],
            }
        return splitting(text)

    edited = EDITED_SETTING.replace(
        "Closing words of the results.", f"{ADDED}\n\nClosing words of the results."
    )
    v, made, _, refused, _ = queued(tmp_path, answer, edited=edited)

    assert made.complete, made.unanswered
    assert refused == []
    (added,) = [
        t
        for t, text in served.items()
        if text.split("\n")[1].startswith("an added block")
    ]
    assert list(served).index(added) > list(served).index(f"{SETTING}#2")
    (edges,) = [a for a in v.store.answers() if a.host == "queue"]
    assert f'⊢ unsupported · "{ADDED}"' in record_file.text(v.store.directory, edges)


@pytest.mark.usefixtures("develop_mode")
def test_a_block_the_draw_missed_is_served_again_at_the_end(
    tmp_path, monkeypatch
) -> None:
    """The end's check hands on what it still finds, as an answer's are
    while the run goes: a theorem the edit added, closed by a split before
    its task with no line (the split's blocks kept open no more, so the
    draw misses it), is served once every other task is settled, its
    problem shown; the session's answer is filed, and the update names the
    block its draw missed."""
    from axiomation import update_queue

    monkeypatch.setattr(update_queue, "without_done", lambda sheet, handles: sheet)
    claim = "Every input is finite."
    served: dict[str, str] = {}

    def answer(text: str) -> tuple[str, dict]:
        task = text.split()[1]
        served[task] = text
        if text.split("\n")[1].startswith("the check still finds"):
            return "answer", {"task": task, "put_on": [f'⊢ unsupported · "{claim}"']}
        return splitting(text)

    edited = EDITED_SETTING.replace(
        "Closing words of the results.",
        f"\\begin{{theorem}}\\label{{thm:finite}}\n{claim}\n\\end{{theorem}}\n\n"
        "Closing words of the results.",
    )
    v, made, _, refused, _ = queued(tmp_path, answer, edited=edited)

    assert made.complete, made.unanswered
    assert refused == []
    assert made.mended == ("thm:finite",)
    mend = served["thm:finite"]
    assert "no support line: thm:finite" in mend
    (edges,) = [a for a in v.store.answers() if a.host == "queue"]
    assert f'⊢ unsupported · "{claim}"' in record_file.text(v.store.directory, edges)


#: The setting's line onto the closing words, which the edit deletes.
LOST = '→ about closing-words-of-the · "We fix a field"'


@pytest.mark.usefixtures("develop_mode")
def test_a_changed_block_is_shown_the_line_it_lost_onto_a_deleted_block(
    tmp_path,
) -> None:
    """The edit deletes the block an edge pointed at and changes the edge's
    source: the source is a changed block, and its task shows the line it
    lost, to put on again or leave gone."""
    served: dict[str, str] = {}

    def answer(text: str) -> tuple[str, dict]:
        served[text.split()[1]] = text
        return narrowing(text)

    edited = EDITED_SETTING.replace("Closing words of the results.\n", "")
    lines = {**CHAIN, SETTING: [*CHAIN[SETTING], LOST]}
    _, made, _, refused, _ = queued(tmp_path, answer, edited=edited, lines=lines)

    assert made.complete and refused == []
    shown = served[SETTING]
    assert shown.split("\n")[1].startswith("a changed block")
    assert f"which the edit deleted: `{LOST}`" in shown
    assert "put it on again onto the block that now says what it pointed at" in shown


@pytest.mark.usefixtures("develop_mode")
def test_a_question_answered_already_is_not_asked_again_as_a_queue(tmp_path) -> None:
    from axiomation.driver import checker
    from axiomation.resolver import Resolver
    from axiomation.runner import Runner
    from axiomation.update import plan
    from test_update import FLAT
    from test_update_single import CARRIED_SETTINGS

    v = two_papers(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    session = FakeHost(
        lambda _: "",
        edits={EDGES_FILE: answering({SETTING: [NARROWED]}, as_assumption)},
    )
    _, made = plan(v.new.document, v.store, CARRIED_SETTINGS)
    Runner(session, Resolver(v.store), 10.0, FLAT).run(
        (made.question,), checker(v.new.document)
    )
    assert made.question.key in v.store.authoritative()

    from axiomation.update import run as run_update
    from axiomation.update_queue import Seats

    again = FakeQueueHost(scripted(narrowing, []))
    settings = dataclasses.replace(
        CARRIED_SETTINGS, paper=v.new.root, edges_queue=Seats((again,))
    )
    result = run_update(
        v.new.document,
        v.store,
        settings,
        Runner(session, Resolver(v.store), 10.0, FLAT),
    )
    assert result.complete
    assert again.sessions == []
    assert [a for a in v.store.answers() if a.host == "queue"] == []


def test_a_problem_of_a_label_inside_another_is_not_its_own() -> None:
    from axiomation.update_queue import _names

    assert _names("lem:a: the block's text changed", "lem:a")
    assert not _names("lem:a:b → uses x: evidence not in lem:a:b", "lem:a")
    assert not _names("lem:a:b: the block's text changed", "lem:a")


@pytest.mark.usefixtures("develop_mode")
def test_a_block_whose_meaning_moved_is_told_so_in_its_task(
    tmp_path, monkeypatch
) -> None:
    """Its words may be the same before and now: the task says what moved,
    under the block's text, by its handle."""
    from axiomation import update

    note = "Its environment now reads as conjecture, where it read as theorem."
    real = update.meaning_notes

    def moved(raw, lineage, meanings):
        found = real(raw, lineage, meanings)
        setting = next(i for i in (meanings or {}) if i.startswith("main.tex:4:"))
        return {**found, setting: note}

    monkeypatch.setattr(update, "meaning_notes", moved)
    served: dict[str, str] = {}

    def answer(text: str) -> tuple[str, dict]:
        served[text.split()[1]] = text
        return narrowing(text)

    _, made, _, refused, _ = queued(tmp_path, answer)

    assert made.complete and refused == []
    shown = served[SETTING]
    assert f"⟦{SETTING}⟧: {note}" in shown
    assert shown.index(note) < shown.index("\nits lines")


@pytest.mark.usefixtures("develop_mode")
def test_words_a_line_quoted_that_are_gone_are_shown_with_where_they_may_be(
    tmp_path, monkeypatch
) -> None:
    """Carry's hint, a line's quoted words this version no longer holds and
    similar words it does, is served in the task of the block it opened."""
    from axiomation import update

    monkeypatch.setattr(
        update,
        "hints_of",
        lambda carried: {
            SETTING: [("the field of scalars", "a field of scalars", SETTING)]
        },
    )
    served: dict[str, str] = {}

    def answer(text: str) -> tuple[str, dict]:
        served[text.split()[1]] = text
        return narrowing(text)

    _, made, _, refused, _ = queued(tmp_path, answer)

    assert made.complete and refused == []
    assert (
        'a line quoted "the field of scalars", which this version no longer holds; '
        f'now perhaps "a field of scalars" in ⟦{SETTING}⟧'
    ) in served[SETTING]


def test_hints_are_kept_by_the_block_they_opened_once_each() -> None:
    from axiomation.questions.edges_file import Carried, Hinted
    from axiomation.update import hints_of

    hint = Hinted("b", "Old  words", "new words", "c")
    carried = Carried("", (), {}, (hint, hint))

    assert hints_of(carried) == {"b": [("Old words", "new words", "c")]}


def test_a_name_read_anew_is_one_a_block_uses() -> None:
    from axiomation.update_queue import _uses

    assert _uses("\\begin{example} a case \\end{example}", "example")
    assert _uses("by \\lemref{a}", "lemref")
    assert not _uses("by \\lemrefs{a}", "lemref")
    assert not _uses("an example of it", "example")
