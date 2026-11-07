"""The queue's core (docs/architecture.md § 2): tasks served one at a
time and answered by typed tools, each answer checked when given, the
working state carried on by each settled answer, the spool, replay in
order, and two steps in lockstep. On `fake_workflow`'s three tasks; no
model is called.
"""

from __future__ import annotations

import io
import json
import threading

import pytest

from axiomation import queue
from axiomation.queue import (
    AGAIN,
    EMPTY,
    ENDED,
    Call,
    Run,
    Served,
    Spool,
    Working,
    advance,
    opened,
)
from axiomation.queue import reads as reads_module
from axiomation.queue.reads import ReadError, Reads
from axiomation.queue.server import respond, serve
from queues.fake_workflow import FACTORY
from test_marks import kept_update
from test_support_search import PROOF_BLOCK
from test_update import SHARP


@pytest.fixture
def paper(tmp_path):
    v, state = kept_update(tmp_path)
    return v.store, state


def served(store, state, spool, step=0, wait=0.0, **options) -> Served:
    run = Run(store, state, FACTORY, options)
    workflow = opened(run).__enter__()
    return Served(workflow, {}, spool, step, f"s{step}", wait=wait, poll=0.01)


def test_a_task_is_served_lean_and_an_answer_checked_at_once(paper, tmp_path) -> None:
    """`next` serves the first task; a wrong answer is refused, nothing
    spooled; a right one is accepted and hands on the next task."""
    store, state = paper
    spool = Spool(tmp_path / "spool.jsonl")
    s = served(store, state, spool)
    assert s.next() == "task t1 (1 of 3)\nsay one\n"
    text, refused = s.call("say", {"task": "t1", "word": "two"})
    assert (
        refused and text == "refused, nothing filed; fix and answer again:\n- say 'one'"
    )
    assert spool.entries() == []
    text, refused = s.call("say", {"task": "t1", "word": "one"})
    assert not refused
    assert text == "accepted: task t1.\n\ntask t2 (2 of 3)\nsay two\n"
    (entry,) = spool.entries()
    assert (entry["task"], entry["tool"], entry["args"]) == (
        "t1",
        "say",
        {"task": "t1", "word": "one"},
    )


def test_the_open_task_is_served_again_and_no_other_is_answered(
    paper, tmp_path
) -> None:
    store, state = paper
    s = served(store, state, Spool(tmp_path / "spool.jsonl"))
    assert s.next() == s.next() == "task t1 (1 of 3)\nsay one\n"
    text, refused = s.call("say", {"task": "t2", "word": "two"})
    assert refused and text == "refused: your open task is t1, not 't2'"


def test_the_queue_says_to_stop_when_every_task_is_settled(paper, tmp_path) -> None:
    store, state = paper
    s = served(store, state, Spool(tmp_path / "spool.jsonl"))
    for task, word in (("t1", "one"), ("t2", "two")):
        s.call("say", {"task": task, "word": word})
    text, _ = s.call("say", {"task": "t3", "word": "three"})
    assert text == f"accepted: task t3.\n\n{EMPTY}"
    assert s.next() == EMPTY


def test_a_settled_line_is_in_the_working_state_its_tasks_and_reads_see(
    paper, tmp_path
) -> None:
    """The proof's answer settles the line that argues the sharpness claim:
    the claim's task leaves the queue, and the reads show the line."""
    store, state = paper
    s = served(store, state, Spool(tmp_path / "spool.jsonl"), argue=True)
    s.call("say", {"task": "t1", "word": "one"})
    text, _ = s.call("say", {"task": "t2", "word": "two"})
    assert text == f"accepted: task t2.\n\n{EMPTY}"
    shown = s.read("edges", {"handle": PROOF_BLOCK})[0]
    assert "settled in this run:" in shown
    assert f"⟦{PROOF_BLOCK}⟧ + → justifies {SHARP}" in shown
    assert f"→ justifies {SHARP}" in s.working.sheet


def test_the_answers_replay_in_their_order_and_only_in_it(paper, tmp_path) -> None:
    """Each key binds the key settled before it: the spool's answers carry
    a fresh working state to the same place, and an answer keyed after
    another task than the one it follows is not taken."""
    store, state = paper
    spool = Spool(tmp_path / "spool.jsonl")
    s = served(store, state, spool)
    s.call("say", {"task": "t1", "word": "one"})
    s.call("say", {"task": "t2", "word": "two"})
    workflow = s.workflow
    position, used = advance(workflow, Working(workflow), spool.calls())
    assert [u.task.id for u in used] == ["t1", "t2"]
    assert all(u.settled for u in used)
    assert (position.task.id, position.key) == ("t3", s.refresh().key)
    # t2's answer alone, without t1's before it, is keyed elsewhere
    t2 = {k: c for k, c in spool.calls().items() if c.args["task"] == "t2"}
    position, used = advance(workflow, Working(workflow), t2)
    assert used == [] and position.task.id == "t1"


def test_a_call_is_recorded_as_its_json() -> None:
    call = Call("say", {"word": "one", "task": "t1"})
    assert call.raw == '{"args": {"task": "t1", "word": "one"}, "tool": "say"}'
    assert Call.read(call.raw) == call


def test_the_ended_run_tells_each_call_to_stop(paper, tmp_path) -> None:
    store, state = paper
    spool = Spool(tmp_path / "spool.jsonl")
    s = served(store, state, spool)
    spool.end("step 1's session ended")
    assert s.next() == ENDED
    assert s.call("say", {"task": "t1", "word": "one"}) == (ENDED, False)


# --- two steps in lockstep -----------------------------------------------------------


def test_the_first_step_waits_until_the_second_settles(paper, tmp_path) -> None:
    """The worker's answer is handed on: its `next` waits, and returns
    "call `next` again" at its bound; the settling step is served the
    word said, and once it settles the worker is served the next task."""
    store, state = paper
    spool = Spool(tmp_path / "spool.jsonl")
    worker = served(store, state, spool, 0, wait=0.05, steps=2)
    settler = served(store, state, spool, 1, wait=0.05, steps=2)
    assert settler.next() == AGAIN
    text, _ = worker.call("say", {"task": "t1", "word": "one"})
    assert text == f"accepted: task t1.\n\n{AGAIN}"
    assert settler.next() == "task t1 (1 of 3)\nconfirm one\n"
    assert worker.next() == AGAIN
    text, _ = settler.call("confirm", {"task": "t1", "word": "one"})
    assert text == f"accepted: task t1.\n\n{AGAIN}"
    assert worker.next() == "task t2 (2 of 3)\nsay two\n"


def test_a_task_the_first_step_passes_is_served_to_the_second_as_drawn(
    paper, tmp_path
) -> None:
    """The worker takes no `t2`: once `t1` settles, the settling step is
    served it as drawn while the worker waits, then the worker `t3`."""
    store, state = paper
    spool = Spool(tmp_path / "spool.jsonl")
    worker = served(store, state, spool, 0, steps=2, passes=["t2"])
    settler = served(store, state, spool, 1, steps=2, passes=["t2"])
    worker.call("say", {"task": "t1", "word": "one"})
    settler.call("confirm", {"task": "t1", "word": "one"})
    assert worker.next() == AGAIN
    assert settler.next() == "task t2 (2 of 3)\nconfirm two\n"
    settler.call("confirm", {"task": "t2", "word": "two"})
    assert worker.next() == "task t3 (3 of 3)\nsay three\n"


def test_the_last_step_takes_every_task(paper) -> None:
    store, state = paper
    run = Run(store, state, FACTORY, {"steps": 2, "confirm_passes": ["t1"]})
    with opened(run) as workflow, pytest.raises(queue.QueueError, match="every task"):
        advance(workflow, Working(workflow), {})


def test_a_waiting_next_returns_once_the_other_step_answers(paper, tmp_path) -> None:
    store, state = paper
    spool = Spool(tmp_path / "spool.jsonl")
    worker = served(store, state, spool, 0, wait=0.05, steps=2)
    settler = served(store, state, spool, 1, wait=10.0, steps=2)
    found: list[str] = []
    waiting = threading.Thread(target=lambda: found.append(settler.next()))
    waiting.start()
    worker.call("say", {"task": "t1", "word": "one"})
    waiting.join(5)
    assert found == ["task t1 (1 of 3)\nconfirm one\n"]


def test_a_wait_returns_before_the_host_s_tool_limit(paper) -> None:
    store, state = paper
    run = Run(store, state, FACTORY, {})
    server = queue.tool_server(run, 0, "s1", run.directory / "spool-1.jsonl", 30.0)
    assert server.call_seconds > 30.0
    assert server.argv[-1] == str(run.directory / "s1.json")
    given = json.loads((run.directory / "s1.json").read_text())
    assert (given["wait_seconds"], given["step"], given["session"]) == (30.0, 0, "s1")


def test_an_answer_handed_on_is_not_in_the_working_state(paper, tmp_path) -> None:
    """The worker's answer on the proof would argue the sharpness claim,
    but it settles only once confirmed: until then the claim stays."""
    store, state = paper
    spool = Spool(tmp_path / "spool.jsonl")
    worker = served(store, state, spool, 0, steps=2, argue=True)
    settler = served(store, state, spool, 1, steps=2, argue=True)
    for task, word in (("t1", "one"),):
        worker.call("say", {"task": task, "word": word})
        settler.call("confirm", {"task": task, "word": word})
    worker.call("say", {"task": "t2", "word": "two"})
    assert f"→ justifies {SHARP}" not in worker.working.sheet
    text, _ = settler.call("confirm", {"task": "t2", "word": "two"})
    assert text == f"accepted: task t2.\n\n{EMPTY}"
    assert f"→ justifies {SHARP}" in settler.working.sheet


# --- the tool server -----------------------------------------------------------------


def test_the_server_speaks_json_rpc_a_message_a_line(paper, tmp_path) -> None:
    store, state = paper
    s = served(store, state, Spool(tmp_path / "spool.jsonl"))
    given = io.StringIO(
        "\n".join(
            json.dumps(m)
            for m in (
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "next", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "say", "arguments": {"task": "t1", "word": "x"}},
                },
                {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
            )
        )
        + "\n"
    )
    out = io.StringIO()
    serve(s, given, out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5]
    assert replies[0]["result"]["serverInfo"]["name"] == "q"
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert names[:2] == ["next", "say"] and "search" in names and "hops" in names
    assert replies[2]["result"] == {
        "content": [{"type": "text", "text": "task t1 (1 of 3)\nsay one\n"}],
        "isError": False,
    }
    assert replies[3]["result"]["isError"] is True
    assert replies[4]["error"]["code"] == -32601


def test_a_tool_that_fails_is_a_refusal_the_session_reads(paper, tmp_path) -> None:
    store, state = paper
    s = served(store, state, Spool(tmp_path / "spool.jsonl"))
    reply = respond(
        s,
        {
            "id": 1,
            "method": "tools/call",
            "params": {"name": "block", "arguments": {"handle": "no-such-block"}},
        },
    )
    assert reply is not None and reply["result"]["isError"] is True
    assert reply["result"]["content"][0]["text"].startswith("refused: ")


# --- the reads -----------------------------------------------------------------------


def test_each_read_is_capped(paper, monkeypatch) -> None:
    store, state = paper
    with opened(Run(store, state, FACTORY, {})) as workflow:
        reads = Reads(workflow.view, workflow.files)
    reads.lines = {"f.md": [f"row {n}" for n in range(1, 151)]}
    shown = reads.view_lines("f.md", 1)
    assert shown.splitlines()[0].startswith("f.md lines 1–100 of 150")
    assert len(shown.splitlines()) == 101
    assert reads.search("row") == "150 hits for /row/: over 50, narrow the pattern"
    assert reads.search("row 1[0-9]$").startswith("10 hits for /row 1[0-9]$/")
    with pytest.raises(ReadError, match="no file 'g.md'"):
        reads.view_lines("g.md", 1)
    with pytest.raises(ReadError):
        reads.search("(")
    monkeypatch.setattr(reads_module, "GRAPH_ITEMS", 1)
    assert reads.hops(PROOF_BLOCK).endswith("… narrow with `edges`")


def test_the_graph_reads_show_the_kept_state_s_own_edges(paper) -> None:
    """`edges` shows what a block rests on and what rests on it, `relied`
    what uses it, each line with its kind, target and quoted words."""
    store, state = paper
    with opened(Run(store, state, FACTORY, {})) as workflow:
        reads = Reads(workflow.view, workflow.files)
    shown = reads.edges(PROOF_BLOCK)
    assert "rests on (3):" in shown
    assert '→ uses def:gain [gain] ref · "By Definition~\\ref{def:gain}"' in shown
    assert f"rested on by (1):\n  {SHARP} → uses {PROOF_BLOCK}" in shown
    assert reads.relied("def:gain").endswith(
        f"rested on by (1):\n  {PROOF_BLOCK} → uses def:gain [gain] ref · "
        '"By Definition~\\ref{def:gain}"\n'
    )


@pytest.mark.parametrize(
    "ref", ["def:gain", "⟦def:gain⟧", " def:gain ", "def-gain", "DEF:Gain"]
)
def test_a_handle_is_found_as_a_reader_writes_it(paper, ref) -> None:
    """In its brackets, with other punctuation or case, as a reader may
    write a label."""
    store, state = paper
    with opened(Run(store, state, FACTORY, {})) as workflow:
        reads = Reads(workflow.view, workflow.files)
    assert reads.handle(reads.resolve(ref)) == "def:gain"


def test_a_part_of_one_handle_finds_it_and_of_several_names_them(paper) -> None:
    store, state = paper
    with opened(Run(store, state, FACTORY, {})) as workflow:
        reads = Reads(workflow.view, workflow.files)
    assert reads.handle(reads.resolve("small-means")) == "small-means-below-one"
    with pytest.raises(ReadError, match="part of several: .*def:gain"):
        reads.resolve("gain")
    with pytest.raises(ReadError, match="^'nowhere' names no block or group$"):
        reads.resolve("nowhere")


def test_a_task_points_at_the_blocks_near_its_own(paper) -> None:
    store, state = paper
    with opened(Run(store, state, FACTORY, {})) as workflow:
        reads = Reads(workflow.view, workflow.files)
        proof = workflow.view.resolve(PROOF_BLOCK)
        pointers = reads.pointers(proof)
        assert workflow.view.resolve("def:gain") in pointers
        assert proof not in pointers


def test_hops_tied_in_rank_read_the_same_whatever_their_sets_order(paper) -> None:
    """A group and its first block tie on distance, paths and place: the
    id breaks the tie, so a task's text, which its key binds, is the same
    in every process."""
    store, state = paper
    with opened(Run(store, state, FACTORY, {})) as workflow:
        reads = Reads(workflow.view, workflow.files)
        group = next(iter(workflow.view.groups))
        first = workflow.view.groups[group]["blocks"][0]
        target = next(b for b in workflow.view.blocks if b not in (group, first))
        ranked = []
        for order in ([group, first], [first, group]):
            reads.adjacent = {target: dict.fromkeys(order), group: {target: None}}
            reads.adjacent[first] = {target: None}
            ranked.append(reads.ranked_hops(target, 1))
    assert ranked[0] == ranked[1] == sorted([group, first])


def test_a_workflow_s_read_of_a_core_read_s_name_is_served_in_its_place(
    paper, tmp_path
) -> None:
    store, state = paper
    s = served(store, state, Spool(tmp_path / "spool.jsonl"), own_block=True)
    names = [t.name for t in s.tools]
    assert names.count("block") == 1
    blocks = [t for t in s.tools if t.name == "block"]
    assert blocks[0].description == "A block as this workflow reads it."
    assert s.call("block", {"handle": PROOF_BLOCK}) == (
        f"this workflow's {PROOF_BLOCK}",
        False,
    )


def test_a_core_read_the_workflow_withholds_is_neither_listed_nor_served(
    paper, tmp_path
) -> None:
    store, state = paper
    s = served(
        store, state, Spool(tmp_path / "spool.jsonl"), withheld=["view", "search"]
    )
    names = {t.name for t in s.tools}
    assert not names & {"view", "search"} and "block" in names
    assert s.call("view", {"file": "main.tex", "line": 1}) == (
        "refused: no tool 'view'",
        True,
    )


def test_a_session_whose_share_is_done_is_told_to_stop_and_the_next_goes_on(
    paper, tmp_path
) -> None:
    """A session answers its share of the tasks and stops; a fresh session
    on the same spool takes the queue on where it stands."""
    store, state = paper
    spool = Spool(tmp_path / "spool.jsonl")
    first = served(store, state, spool, session_tasks=2)
    first.call("say", {"task": "t1", "word": "one"})
    text, _ = first.call("say", {"task": "t2", "word": "two"})
    assert text == f"accepted: task t2.\n\n{queue.SHARE_DONE}"
    assert first.next() == queue.SHARE_DONE
    fresh = served(store, state, spool, session_tasks=2)
    assert fresh.next() == "task t3 (3 of 3)\nsay three\n"


def test_a_first_step_s_share_ends_once_the_second_settles_its_last_task(
    paper, tmp_path
) -> None:
    """The worker's share is one task: it waits while the settling step
    confirms that task, and is told to stop only where it would be served
    the next, so the pair's last task settles."""
    store, state = paper
    spool = Spool(tmp_path / "spool.jsonl")
    worker = served(store, state, spool, 0, wait=0.05, steps=2, session_tasks=1)
    settler = served(store, state, spool, 1, wait=0.05, steps=2, session_tasks=1)
    text, _ = worker.call("say", {"task": "t1", "word": "one"})
    assert text == f"accepted: task t1.\n\n{AGAIN}"
    assert worker.next() == AGAIN
    # the settler waits for the next task; the worker's share is done there
    text, _ = settler.call("confirm", {"task": "t1", "word": "one"})
    assert text == f"accepted: task t1.\n\n{AGAIN}"
    assert worker.next() == queue.SHARE_DONE


def test_a_session_is_given_the_files_its_workflow_lays_in_its_folder(
    paper, tmp_path
) -> None:
    store, state = paper
    run = Run(store, state, FACTORY, {"files": {"paper.md": "the text"}})
    with opened(run) as workflow:
        step = workflow.steps[0]
        server = queue.tool_server(run, 0, "s0", tmp_path / "spool.jsonl", 1.0)
        asked = queue.queue_session(step, server, None, workflow.serving.files)
    assert asked.files == {"paper.md": "the text"}


def test_a_workflow_s_own_read_is_served_beside_the_core_s(paper, tmp_path) -> None:
    """A read the workflow adds is listed with the core's and answered at
    the working state; its refusal is the session's to read."""
    store, state = paper
    s = served(store, state, Spool(tmp_path / "spool.jsonl"), counted=True)
    assert "count" in {t.name for t in s.tools}
    s.next()
    s.call("say", {"task": "t1", "word": "one"})
    assert s.call("count", {"of": "settled"}) == ("1 settled", False)
    assert s.call("count", {"of": "open"}) == (
        "refused: count only what is settled",
        True,
    )
    plain = served(store, state, Spool(tmp_path / "plain.jsonl"))
    assert "count" not in {t.name for t in plain.tools}
    assert plain.call("count", {"of": "settled"}) == ("refused: no tool 'count'", True)


def test_a_workflow_in_the_paper_s_order_is_served_so(
    paper, tmp_path, monkeypatch
) -> None:
    """Served nearest on the graph unless the workflow asks for the
    paper's order alone."""
    store, state = paper
    monkeypatch.setattr(queue, "_nearest", lambda open_, last, reads: open_[-1])

    def order(**options) -> list[str]:
        s = served(store, state, Spool(tmp_path / f"{len(options)}.jsonl"), **options)
        said = []
        while (text := s.next()) != EMPTY:
            task, word = text.split()[1], text.splitlines()[1].split()[1]
            said.append(task)
            s.call("say", {"task": task, "word": word})
        return said

    assert order() == ["t3", "t2", "t1"]
    assert order(paper_order=True) == ["t1", "t2", "t3"]


def test_a_label_the_cut_leaves_out_is_placed_where_the_reader_found_it(
    tmp_path,
) -> None:
    """A LaTeX `\\label` is not in its block's text; the reader's place of
    it puts it in the innermost block holding it, so a `\\ref` to it points
    there."""
    from types import SimpleNamespace

    def block(ident: str, start: int, end: int) -> dict:
        return {
            "id": ident,
            "extent": {"file": "main.tex", "start": [start, 1], "end": [end, 80]},
        }

    view = SimpleNamespace(
        blocks={
            b["id"]: b
            for b in (block("b1", 1, 9), block("b2", 3, 4), block("b3", 10, 12))
        },
        edges=[],
        groups={},
        groups_of={},
        texts={
            "b1": "",
            "b2": "The bound holds.",
            "b3": "By Lemma \\ref{lem:bound}, so.",
        },
        export={
            "document": {
                "labels": [
                    {
                        "name": "lem:bound",
                        "at": {"file": "main.tex", "start": [3, 20], "end": [3, 36]},
                    }
                ]
            }
        },
    )
    reads = Reads(view, tmp_path)
    assert reads.labels == {"lem:bound": "b2"}
    assert reads.referenced("b3") == ["b2"]


def test_a_read_answered_as_a_picture_is_served_as_mcp_image_content() -> None:
    from axiomation.queue.server import content

    assert content("text") == [{"type": "text", "text": "text"}]
    shown = content(queue.Image(b"\x89PNG", "work page index 2"))
    assert shown[0] == {"type": "text", "text": "work page index 2"}
    assert shown[1] == {"type": "image", "data": "iVBORw==", "mimeType": "image/png"}
